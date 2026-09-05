#!/bin/bash
# Crucible 内网一键部署 (拖包版, WSL/Linux)
# 用法: 把本脚本与三个 tar 放同一目录, 填好【部署参数】后执行: bash 内网部署.sh
# 前置: Docker Desktop (WSL2 backend, 原生 amd64 无模拟); 内网 LLM 端点可达
set -e
cd "$(dirname "$0")"

# ============ 部署参数 (按你的环境修改) ============
LLM_BASE="http://<内网LLM地址>/v1"
LLM_API_KEY="<内网LLM key>"
LLM_MODEL="deepseek-chat"
DATA_ROOT="/home/$USER/kb-data"      # 项目数据目录 (llm-wiki 与 crucible 共享挂载)
LLM_WIKI_STATE="/home/$USER/kb-state" # llm-wiki 状态目录
PG_HOST="host.docker.internal"        # postgres 地址 (容器内视角)
PG_URL="postgresql+asyncpg://crucible:crucible@${PG_HOST}:5432/crucible"
PIP_SOURCE=""      # 内网 pip 源 (如 http://<nexus>/repository/pypi-group/simple), 空=不覆盖
PIP_TRUSTED_HOST=""  # 内源是 http 时必须填主机名 (pip 默认拒绝非 HTTPS)
# ==================================================

echo "[1/6] 加载镜像"
for t in py-llm-wiki-amd64.tar crucible-cpu.tar docreader-final.tar; do
  if [ -f "$t" ]; then docker load -i "$t"; else echo "  ⚠ 缺少 $t (跳过)"; fi
done
if [ -f postgres-amd64.tar ]; then docker load -i postgres-amd64.tar; fi

# 模型缓存 (bge 嵌入 + tiktoken): 解到宿主机缓存, crucible 挂载使用
if [ -f hf-cache.tar.gz ]; then
  mkdir -p "$HOME/.cache/huggingface/hub"
  tar xzf hf-cache.tar.gz -C "$HOME/.cache/huggingface/hub" && echo "  ✓ bge 模型缓存已解包"
fi
if [ -f tiktoken-cache.tar.gz ]; then
  mkdir -p "$HOME/.cache/tiktoken"
  tar xzf tiktoken-cache.tar.gz -C "$HOME/.cache/tiktoken" && echo "  ✓ tiktoken 缓存已解包"
fi

echo "[2/6] 启动 postgres"
docker rm -f crucible-pg 2>/dev/null || true
docker run -d --name crucible-pg --restart unless-stopped \
  -e POSTGRES_USER=crucible -e POSTGRES_PASSWORD=crucible -e POSTGRES_DB=crucible \
  -p 5432:5432 -v crucible-pg-data:/var/lib/postgresql/data postgres:16-alpine-amd64

echo "[3/6] 启动 llm-wiki (含 UI, 同端口 19828)"
mkdir -p "$LLM_WIKI_STATE"
APPSTATE="$LLM_WIKI_STATE/app-state.json"
if [ ! -f "$APPSTATE" ]; then
  cat > "$APPSTATE" <<'EOF'
{
  "projectRegistry": {},
  "apiConfig": {"allowUnauthenticated": true, "allowLanAccess": true}
}
EOF
  echo "  → 已生成 $APPSTATE (项目注册见文末指引)"
fi
docker rm -f crucible-llmwiki 2>/dev/null || true
docker run -d --name crucible-llmwiki --restart unless-stopped \
  -p 19828:19828 \
  -v "$LLM_WIKI_STATE:/data" -v "$DATA_ROOT:/projects" \
  -e "LLM_WIKI_LLM_BASE=$LLM_BASE" -e "LLM_WIKI_LLM_API_KEY=$LLM_API_KEY" -e "LLM_WIKI_LLM_MODEL=$LLM_MODEL" \
  py-llm-wiki:amd64

echo "[4/6] 启动 docreader (MinerU: 根镜像 + 薄应用层)"
# 根镜像已在 [1/6] 加载 (tar 内含 docreader-base:amd64); 应用层从代码秒级构建
# 布局约定: 本摆渡目录与 crucible 仓库同级 (如 ~/deploy-bundle 与 ~/crucible)
REPO_DIR=""
for cand in "../crucible" "../Crucible" ".."; do
  if [ -f "$cand/deploy/Dockerfile.docreader" ]; then REPO_DIR="$cand"; break; fi
done
if [ -n "$REPO_DIR" ]; then
  cd "$REPO_DIR"
  docker build -f deploy/Dockerfile.docreader -t docreader-app:latest .
  cd - > /dev/null
else
  echo "  ⚠ 未找到 crucible 仓库 (docreader 薄层), 直接用根镜像运行"
  docker tag docreader-base:amd64 docreader-app:latest
fi
docker rm -f crucible-docreader 2>/dev/null || true
docker run -d --name crucible-docreader --restart unless-stopped -p 8081:8081 \
  docreader-app:latest

echo "[5/6] 启动 crucible 融合服务"
mkdir -p "$DATA_ROOT"
docker rm -f crucible-app 2>/dev/null || true
# --add-host host-gateway: host.docker.internal 在非 Docker Desktop
# 环境 (原生 docker/部分 WSL 配置) 不解析, 显式映射到宿主机网关
docker run -d --name crucible-app --restart unless-stopped \
  --add-host=host.docker.internal:host-gateway \
  -p 8080:8080 \
  ${PIP_SOURCE:+-e "PIP_INDEX_URL=$PIP_SOURCE"} \
  ${PIP_TRUSTED_HOST:+-e "PIP_TRUSTED_HOST=$PIP_TRUSTED_HOST"} \
  -e "CRUCIBLE_DATABASE_URL=$PG_URL" \
  -e "CRUCIBLE_WIKI_BASE=http://host.docker.internal:19828" \
  -e "CRUCIBLE_DOCREADER_BASE=http://host.docker.internal:8081" \
  -e "CRUCIBLE_LLM_BASE=$LLM_BASE" -e "CRUCIBLE_LLM_API_KEY=$LLM_API_KEY" -e "CRUCIBLE_LLM_MODEL=$LLM_MODEL" \
  -e "CRUCIBLE_EMBED_MODEL=BAAI/bge-m3" -e "CRUCIBLE_EMBED_DIM=1024" \
  -v "$DATA_ROOT:/data" \
  -v "$HOME/.cache/huggingface:/root/.cache/huggingface" \
  -v "$HOME/.cache/tiktoken:/root/.cache/tiktoken" \
  -e "HF_HUB_OFFLINE=1" \
  deploy-crucible:amd64-cpu

echo "[6/6] 健康检查"
sleep 12
echo "  crucible:  $(curl -s http://localhost:8080/health | python3 -c 'import json,sys; print(json.load(sys.stdin)["status"])' 2>/dev/null || echo 未响应)"
echo "  llm-wiki:  $(curl -s http://localhost:19828/health | python3 -c 'import json,sys; print(json.load(sys.stdin)["status"])' 2>/dev/null || echo 未响应)"
echo "  docreader: $(curl -s http://localhost:8081/health | python3 -c 'import json,sys; print(json.load(sys.stdin)["status"])' 2>/dev/null || echo 未响应)"

echo ""
echo "部署完成。接下来:"
echo "  1. crucible 注册项目 (wiki_sync 默认开: 自动在 llm-wiki 建项目+模板+回填 uuid):"
echo "     curl -X POST http://localhost:8080/projects -H 'Content-Type: application/json' -d '{\"id\":\"<产品名>\",\"path\":\"/data/<产品名>\",\"rag_workdir\":\"/data/<产品名>/.lightrag\"}'"
echo "     (目录不存在时由 llm-wiki 自动创建 schema.md 等模板; 父目录即 DATA_ROOT)"
echo "  2. 打开融合台: http://localhost:8080 (上传文档走 MinerU 双通道)"
