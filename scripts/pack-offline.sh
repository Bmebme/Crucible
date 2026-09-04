#!/bin/bash
# 内网离线部署打包: 镜像 + 模型缓存 + 代码 → 单个目录, 拷进内网 docker load 即可
# 用法: scripts/pack-offline.sh [输出目录]   (默认 ./offline-bundle)
# 注意: 镜像架构与打包机器一致 (Apple Silicon = arm64; 内网若是 x86_64 服务器
#       需在打包前重建镜像: docker compose build --platform linux/amd64, 见 deploy.md)
set -e
cd "$(dirname "$0")/.."
OUT="${1:-offline-bundle}"
mkdir -p "$OUT"

echo "[1/4] 导出镜像"
# amd64 摆渡镜像 (内网 x86_64 服务器); 若内网是 arm64 改用 latest 系列
docker save deploy-crucible:amd64 deploy-docreader:amd64-with-models postgres:16-alpine-amd64 \
  -o "$OUT/images.tar" 2>/dev/null || docker save deploy-crucible:latest deploy-docreader:with-models postgres:16-alpine -o "$OUT/images.tar"
echo "  → $OUT/images.tar ($(du -h "$OUT/images.tar" | cut -f1))"

echo "[2/4] 模型缓存 (bge 嵌入 + tiktoken)"
tar czf "$OUT/hf-cache.tar.gz" -C "$HOME/.cache/huggingface" . 2>/dev/null || true
tar czf "$OUT/tiktoken-cache.tar.gz" -C "$HOME/.cache/tiktoken" . 2>/dev/null || true
echo "  → hf-cache.tar.gz / tiktoken-cache.tar.gz"

echo "[3/4] 代码与配置"
tar czf "$OUT/repo.tar.gz" \
  --exclude='frontend/node_modules' --exclude='deploy/.env' --exclude='offline-bundle' \
  --exclude='.git' --exclude='*.pyc' --exclude='__pycache__' . 2>/dev/null || true
echo "  → repo.tar.gz"

echo "[4/4] 内网恢复脚本"
cat > "$OUT/restore-intranet.sh" <<'RESTORE'
#!/bin/bash
# 在内网机器上执行: 恢复镜像 + 缓存 + 代码
set -e
cd "$(dirname "$0")"
docker load -i images.tar
mkdir -p ~/.cache/huggingface ~/.cache/tiktoken
tar xzf hf-cache.tar.gz -C ~/.cache/huggingface 2>/dev/null || true
tar xzf tiktoken-cache.tar.gz -C ~/.cache/tiktoken 2>/dev/null || true
mkdir -p crucible && tar xzf repo.tar.gz -C crucible
cat <<'DONE'
恢复完成。下一步:
  1. cd crucible/deploy && cp .env.example .env
     编辑 .env: LLM_API_KEY / LLM_BASE 指向内网 LLM 端点 (OpenAI 兼容)
     DATA_ROOT 指向项目数据目录; CRUCIBLE_WIKI_BASE 视 llm-wiki 位置
  2. docker compose up -d
  3. 注册项目 (容器内路径 /data/<name>) 后访问 http://<内网IP>:8080
DONE
RESTORE
chmod +x "$OUT/restore-intranet.sh"
echo "完成: $OUT/ 拷进内网后执行 ./restore-intranet.sh"
