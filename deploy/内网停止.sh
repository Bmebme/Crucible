#!/bin/bash
# Crucible 内网停止/清理: 停掉并移除四个容器 (数据卷不删)
# 用法: bash 内网停止.sh [--with-data]   (--with-data 连 PG 数据卷一起删, 慎用)
set -e
WITH_DATA="${1:-}"

echo "停止容器..."
for c in crucible-app crucible-docreader crucible-llmwiki crucible-pg; do
  docker rm -f "$c" 2>/dev/null && echo "  已移除 $c" || echo "  跳过 $c (不存在)"
done

if [ "$WITH_DATA" = "--with-data" ]; then
  echo "删除 PG 数据卷 (crucible-pg-data)..."
  docker volume rm crucible-pg-data 2>/dev/null || true
fi

echo "完成。容器已停; 数据目录与镜像保留, 重新部署直接 bash 内网部署.sh"
