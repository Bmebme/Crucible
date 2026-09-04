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

# 依赖层与代码层分离: 代码变更只失效最后一层 (秒级重建), 不再重下 torch
COPY pyproject.toml ./
RUN pip install --no-cache-dir \
        lightrag-hku>=1.5.0 sentence-transformers>=3.0 \
        fastapi>=0.115 uvicorn>=0.30 "sqlalchemy[asyncio]>=2.0" asyncpg>=0.29 \
        pydantic-settings>=2.3 python-multipart>=0.0.9 alembic>=1.13 greenlet \
        httpx>=0.27 pydantic>=2.7 pyyaml>=6.0

# 代码层 (变更只需重建这一层)
COPY src ./src
COPY backend ./backend
COPY frontend/dist ./frontend/dist
RUN pip install --no-cache-dir --no-deps -e .

EXPOSE 8080
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8080", "--app-dir", "backend"]
