#!/bin/bash
# Crucible 一键引导: clone 后跑这一个脚本即可起全套服务
# 用法: cd crucible/deploy && ./bootstrap.sh
# 前置: Docker Desktop/Engine; 网络能访问 daocloud/tuna/aliyun (国内约定, deploy.md §4)
set -e
cd "$(dirname "$0")"

echo "[1/5] .env 配置"
if [ ! -f .env ]; then
  cp .env.example .env
  echo "  → 已生成 .env, 请填入 LLM_API_KEY 后重新执行"
  exit 0
fi
grep -q "sk-xxx" .env && { echo "  ⚠ .env 里 LLM_API_KEY 还是占位符 sk-xxx, 请先填写"; exit 1; }

echo "[2/5] 基础镜像 (Docker Hub 被墙时经 daocloud)"
for img in postgres:16-alpine python:3.12-slim; do
  if docker image inspect "$img" >/dev/null 2>&1; then
    echo "  ✓ $img 已存在"
  else
    mirror="docker.m.daocloud.io/library/$img"
    echo "  → 拉取 $mirror"
    docker pull "$mirror" >/dev/null && docker tag "$mirror" "$img"
  fi
done

echo "[3/5] 构建 (crucible 首次 30-60 分钟含 torch; docreader-light 快)"
docker compose build crucible docreader

echo "[4/5] 启动"
docker compose up -d

echo "[5/5] 健康检查"
sleep 10
curl -sf http://127.0.0.1:8080/health >/dev/null \
  && echo "  ✓ http://127.0.0.1:8080/health ok" \
  || echo "  ⚠ health 未响应, 查: docker compose logs crucible"

cat <<'DONE'

完成。接下来:
  1. 注册项目 (容器内路径 /data/<name>):
     curl -X POST http://127.0.0.1:8080/projects -H 'Content-Type: application/json' \
       -d '{"id":"<name>","path":"/data/<name>","wiki_project_id":"<llm-wiki 侧 id>"}'
  2. 打开 http://127.0.0.1:8080
  3. (可选) MinerU 完整解析升级: docs/deploy.md §3.3
DONE
