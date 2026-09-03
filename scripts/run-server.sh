#!/bin/bash
# 本地开发服务守护: uvicorn 崩溃自动重启 (docker 化完成后用 compose 替代)
# 用法: scripts/run-server.sh
set -u

PY="/opt/homebrew/Caskroom/miniconda/base/envs/crucible/bin/python3"
cd "$(dirname "$0")/.."

export CRUCIBLE_DATABASE_URL="${CRUCIBLE_DATABASE_URL:-postgresql+asyncpg://crucible:crucible@127.0.0.1:5432/crucible}"
export CRUCIBLE_WIKI_BASE="${CRUCIBLE_WIKI_BASE:-http://127.0.0.1:19828}"

while true; do
  echo "[$(date '+%F %T')] starting uvicorn..."
  "$PY" -m uvicorn app.main:app --app-dir backend --host 127.0.0.1 --port 8080
  echo "[$(date '+%F %T')] uvicorn exited ($?), restarting in 3s..."
  sleep 3
done
