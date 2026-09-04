#!/bin/bash
# 把 MinerU 模型从现有 arm64 容器拷进 amd64 容器并 commit 成镜像
# (模型文件是 onnx/safetensors 数据, 架构无关; 免 3GB 重下载)
# 前置: deploy-docreader:amd64-base 已构建; deploy-docreader-1 (arm64 带模型) 在跑
set -e
echo "[1/3] 起 amd64 容器"
docker rm -f docreader-amd64-bake 2>/dev/null || true
docker create --name docreader-amd64-bake --platform linux/amd64 deploy-docreader:amd64-base
echo "[2/3] 拷贝模型缓存 (arm64 → amd64)"
docker cp deploy-docreader-1:/root/.cache/huggingface /tmp/hf-bake && \
  docker cp /tmp/hf-bake docreader-amd64-bake:/root/.cache/huggingface
rm -rf /tmp/hf-bake
echo "[3/3] commit 镜像"
docker commit docreader-amd64-bake deploy-docreader:amd64-with-models
docker rm -f docreader-amd64-bake
docker images | grep amd64-with-models
echo "完成: deploy-docreader:amd64-with-models"
