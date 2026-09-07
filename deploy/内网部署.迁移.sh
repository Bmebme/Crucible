#!/bin/bash
# 内网部署.迁移.sh —— 老数据非破坏迁移版部署
#
# 与 内网部署.sh 的区别:
#   1. 数据路径全部显式传参, 无静默默认 (不碰意外的老挂载路径)
#   2. 支持 --copy 模式: 老数据先复制到新目录再挂载, 老目录全程只读
#   3. 容器 (root) 写入的数据自动 chown 回当前用户 (修 permission denied)
#
# 用法 (deploy-bundle 目录下执行):
#   bash 内网部署.迁移.sh <数据根目录> <llm-wiki状态目录> [--copy <老数据目录>]
#   例 (原地挂载老数据):
#     bash 内网部署.迁移.sh /home/$USER/kb-data /home/$USER/kb-state
#   例 (复制迁移, 老目录不动):
#     bash 内网部署.迁移.sh /home/$USER/kb-data /home/$USER/kb-state --copy /mnt/c/old-kb
set -e
cd "$(dirname "$0")"

DATA_ROOT="${1:?用法: bash 内网部署.迁移.sh <数据根> <状态目录> [--copy <老数据目录>]}"
LLM_WIKI_STATE="${2:?缺少状态目录参数}"
COPY_FROM=""
if [ "$3" = "--copy" ]; then COPY_FROM="${4:?--copy 需要老数据目录参数}"; fi

# ============ 部署参数 (与 内网部署.sh 相同) ============
LLM_BASE="http://<内网LLM地址>/v1"
LLM_API_KEY="<内网LLM key>"
LLM_MODEL="deepseek-chat"
PG_HOST="host.docker.internal"
PG_URL="postgresql+asyncpg://crucible:crucible@${PG_HOST}:5432/crucible"
PIP_SOURCE=""
PIP_TRUSTED_HOST=""
# ==================================================

SUDO=""
if [ "$(id -u)" != "0" ]; then
  command -v sudo >/dev/null && SUDO="sudo"
fi

echo "[0/6] 数据准备"
if [ -n "$COPY_FROM" ]; then
  if [ -e "$DATA_ROOT" ]; then
    echo "  ✗ 目标已存在: $DATA_ROOT (复制模式要求目标不存在, 防覆盖)"
    exit 1
  fi
  echo "  复制 $COPY_FROM → $DATA_ROOT (老目录只读不动)"
  $SUDO cp -a "$COPY_FROM" "$DATA_ROOT"
  echo "  ✓ 复制完成"
else
  mkdir -p "$DATA_ROOT"
  echo "  原地使用: $DATA_ROOT (不动内容)"
fi
mkdir -p "$LLM_WIKI_STATE"
# 容器 root 写入 → 文件变 root 属主 → 用户再操作 permission denied:
# 统一把属主交还当前用户 (每次部署都修一遍, 幂等)
$SUDO chown -R "$(id -u):$(id -g)" "$DATA_ROOT" "$LLM_WIKI_STATE" 2>/dev/null || true
echo "  ✓ 属主已交还 $(whoami)"

echo "[1/6] 加载镜像"
for t in py-llm-wiki-amd64.tar crucible-cpu.tar docreader-final.tar; do
  if [ -f "$t" ]; then docker load -i "$t"; else echo "  ⚠ 缺少 $t (跳过)"; fi
done
if [ -f postgres-amd64.tar ]; then docker load -i postgres-amd64.tar; fi

echo "[2/6] 启动 postgres"
docker rm -f crucible-pg 2>/dev/null || true
docker run -d --name crucible-pg --restart unless-stopped \
  -e POSTGRES_USER=crucible -e POSTGRES_PASSWORD=crucible -e POSTGRES_DB=crucible \
  -p 5432:5432 -v crucible-pg-data:/var/lib/postgresql/data postgres:16-alpine-amd64

echo "[3/6] 启动 llm-wiki (薄层优先)"
PLATFORM_ARG=""
if [ "$(uname -m)" != "x86_64" ]; then PLATFORM_ARG="--platform linux/amd64"; fi
APPSTATE="$LLM_WIKI_STATE/app-state.json"
if [ ! -f "$APPSTATE" ]; then
  cat > "$APPSTATE" <<'EOF'
{
  "projectRegistry": {},
  "apiConfig": {"allowUnauthenticated": true, "allowLanAccess": true}
}
EOF
fi
python3 - "$APPSTATE" "$LLM_BASE" "$LLM_API_KEY" "$LLM_MODEL" <<'PYEOF'
import json, sys
p, base, key, model = sys.argv[1:5]
try:
    s = json.load(open(p, encoding="utf-8"))
except Exception:
    s = {}
s.setdefault("apiConfig", {}).update({"allowUnauthenticated": True, "allowLanAccess": True})
preset_id = "custom-crucible-deploy"
s["llmConfig"] = {"provider": "custom", "apiKey": key, "model": model,
                  "customEndpoint": base, "maxContextSize": 204800}
s["customLlmPresets"] = [{"id": preset_id, "label": "Crucible 部署 (内网 LLM)"}]
s["providerConfigs"] = {preset_id: {"apiKey": key, "model": model, "baseUrl": base,
                                    "apiMode": "chat_completions", "maxContextSize": 204800}}
s["activePresetId"] = preset_id
json.dump(s, open(p, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
print(f"  → llmConfig + 自定义 preset 已写入 {p}")
PYEOF
PYWIKI_REPO=""
for cand in "../py-llm-wiki" "../Py-llm-wiki" ".."; do
  if [ -f "$cand/Dockerfile.thin" ]; then PYWIKI_REPO="$cand"; break; fi
done
if [ -n "$PYWIKI_REPO" ]; then
  cd "$PYWIKI_REPO"
  if docker build $PLATFORM_ARG -f Dockerfile.thin -t py-llm-wiki:amd64 .; then
    echo "  ✓ py-llm-wiki 薄层构建完成"
  else
    echo "  ⚠ 薄层失败, 用基础镜像"
  fi
  cd - > /dev/null
fi
docker rm -f crucible-llmwiki 2>/dev/null || true
docker run -d --name crucible-llmwiki --restart unless-stopped \
  -p 19828:19828 \
  -v "$LLM_WIKI_STATE:/data" -v "$DATA_ROOT:/projects" \
  -e "LLM_WIKI_LLM_BASE=$LLM_BASE" -e "LLM_WIKI_LLM_API_KEY=$LLM_API_KEY" -e "LLM_WIKI_LLM_MODEL=$LLM_MODEL" \
  py-llm-wiki:amd64

echo "[4/6] 启动 docreader"
REPO_DIR=""
for cand in "../crucible" "../Crucible" ".."; do
  if [ -f "$cand/deploy/Dockerfile.docreader" ]; then REPO_DIR="$cand"; break; fi
done
if [ -n "$REPO_DIR" ]; then
  cd "$REPO_DIR"
  docker build -f deploy/Dockerfile.docreader -t docreader-app:latest .
  cd - > /dev/null
else
  docker tag docreader-base:amd64 docreader-app:latest
fi
docker rm -f crucible-docreader 2>/dev/null || true
docker run -d --name crucible-docreader --restart unless-stopped -p 8081:8081 \
  docreader-app:latest

echo "[5/6] 启动 crucible (薄层优先)"
mkdir -p "$DATA_ROOT"
if [ -n "$REPO_DIR" ]; then
  cd "$REPO_DIR"
  if docker build $PLATFORM_ARG -f deploy/Dockerfile.crucible -t deploy-crucible:amd64-cpu .; then
    echo "  ✓ crucible 薄层构建完成"
  else
    echo "  ⚠ 薄层失败, 用基础镜像"
    docker tag crucible-base:amd64 deploy-crucible:amd64-cpu
  fi
  cd - > /dev/null
else
  docker tag crucible-base:amd64 deploy-crucible:amd64-cpu
fi
docker rm -f crucible-app 2>/dev/null || true
docker run -d --name crucible-app --restart unless-stopped \
  --add-host=host.docker.internal:host-gateway \
  -p 8080:8080 \
  ${PIP_SOURCE:+-e "PIP_INDEX_URL=$PIP_SOURCE"} \
  ${PIP_TRUSTED_HOST:+-e "PIP_TRUSTED_HOST=$PIP_TRUSTED_HOST"} \
  -e "CRUCIBLE_DATABASE_URL=$PG_URL" \
  -e "CRUCIBLE_WIKI_BASE=http://host.docker.internal:19828" \
  -e "CRUCIBLE_DOCREADER_BASE=http://host.docker.internal:8081" \
  -e "CRUCIBLE_LLM_BASE=$LLM_BASE" -e "CRUCIBLE_LLM_API_KEY=$LLM_API_KEY" -e "CRUCIBLE_LLM_MODEL=$LLM_MODEL" \
  -v "$DATA_ROOT:/data" \
  -v "$HOME/.cache/tiktoken:/root/.cache/tiktoken" \
  -e "HF_HUB_OFFLINE=1" \
  -e "TIKTOKEN_CACHE_DIR=/root/.cache/tiktoken" \
  -e "CRUCIBLE_API_BASE=http://127.0.0.1:8080" \
  deploy-crucible:amd64-cpu

echo "[6/6] 健康检查"
sleep 12
echo "  crucible:  $(curl -s http://localhost:8080/health | head -c 60)"
echo "  llm-wiki:  $(curl -s http://localhost:19828/health | head -c 60)"
echo "  docreader: $(curl -s http://localhost:8081/health | head -c 60)"
echo ""
echo "部署完成。接下来:"
echo "  1. crucible 注册项目: curl -X POST http://localhost:8080/projects -H 'Content-Type: application/json' -d '{\"id\":\"<项目名>\",\"path\":\"/data/<项目名>\"}'"
echo "  2. 存量 wiki md 补 RAG: cd ~/crucible && python3 scripts/batch_reingest.py <项目名>"
