# Crucible 融合服务 (backend + frontend 静态资源)
FROM python:3.12-slim

# 国内镜像加速 (构建期)
ENV PIP_INDEX_URL=https://pypi.tuna.tsinghua.edu.cn/simple

WORKDIR /srv

# 系统依赖: 仅 libgomp (torch 运行时); 全部 Python 依赖走 wheel, 无需编译工具链
# (build-essential 会撑爆 Docker Desktop 内存限额, 已实测踩坑)
RUN apt-get update && apt-get install -y --no-install-recommends \
        libgomp1 curl \
    && rm -rf /var/lib/apt/lists/*

# 拷代码 + 安装 (editable 安装需要 src 在场; rag 装失败则降级为基础包)
COPY pyproject.toml ./
COPY src ./src
COPY backend ./backend
COPY frontend/dist ./frontend/dist
RUN pip install --no-cache-dir -e ".[rag,server]" || pip install --no-cache-dir -e .

EXPOSE 8080
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8080", "--app-dir", "backend"]
