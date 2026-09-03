# Crucible 融合服务 (backend + frontend 静态资源)
FROM python:3.12-slim

# 国内镜像加速 (构建期)
ENV PIP_INDEX_URL=https://pypi.tuna.tsinghua.edu.cn/simple

WORKDIR /srv

# 系统依赖: lightrag 需要 build 工具链 (Cython/numpy), libgomp 供 torch
RUN apt-get update && apt-get install -y --no-install-recommends \
        build-essential libgomp1 curl \
    && rm -rf /var/lib/apt/lists/*

# 先装依赖再拷代码 (利用层缓存)
COPY pyproject.toml ./
RUN pip install --no-cache-dir -e ".[rag,server]" || pip install --no-cache-dir -e .

COPY src ./src
COPY backend ./backend
COPY frontend/dist ./frontend/dist
RUN pip install --no-cache-dir -e .

EXPOSE 8080
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8080", "--app-dir", "backend"]
