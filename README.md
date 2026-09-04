# Crucible 融合检索层 + 服务化工程

llm_wiki 页面引擎 × LightRAG 实体图引擎的**判别、召回与合并**工程实现。
设计依据: 设计文档 §5（../knowledge-fusion/DESIGN-search-strategy.md）、
决策记录 [docs/decisions.md](docs/decisions.md)、工程化方案
[docs/engineering-plan.md](docs/engineering-plan.md)。

```
query
  → classifier      判别器: 规则 + LLM 三分类 (Q1 枚举·全 / Q2 机制·准 / Q3 经验·可信)
  → rewrite         多轮追问指代消解 (WeKnora QUERY_UNDERSTAND 落地)
  → engines         双引擎并行召回
       wiki_engine  llm_wiki HTTP API (混合检索 / 页面清单 / frontmatter)
       rag_engine   LightRAG (实体枚举 / hybrid 查询, 内网版同契约替换)
  → merge           按类型合并
       m1_union         Q1: 并集 + 差异清单 + L2 词典 / L3 LLM 实体消解
       m2_consistency   Q2: LLM 一致性比对 (只比对不重写, 冲突对峙不裁决)
       m3_state         Q3: verify_state 加权排序 (原样引用)
  → orchestrator    编排 + 统一响应 (结果 + 溯源 + 冲突/差异)
```

## 目录

```
src/crucible/    核心融合层 (core 分支归档: v0.1.0-core)
backend/app/     FastAPI 服务 + PG 元数据层 + 上传管线 (P1/P3a)
frontend/        Vue3 + Element Plus (P4a: 查询工作台/上传中心/项目中心)
deploy/          docker-compose + Dockerfile + .env (统一 LLM 唯一事实源)
docs/            decisions.md 决策记录 · engineering-plan.md 工程化方案
tests/           29 个核心单测
```

## 快速开始 (服务化形态)

### 方式 A: 本地开发

```bash
# 依赖: conda env crucible (Python 3.12)
#   pip install -e ".[rag,server,dev]"
# PG: 用 docker 起 (镜像已本地缓存, 见 deploy/)
cd deploy && docker compose up -d postgres

# 后端 (deploy/.env 提供统一 LLM 配置)
cd .. && CRUCIBLE_DATABASE_URL="postgresql+asyncpg://crucible:crucible@127.0.0.1:5432/crucible" \
  CRUCIBLE_WIKI_BASE="http://127.0.0.1:19828" \
  uvicorn app.main:app --app-dir backend --host 127.0.0.1 --port 8080

# 打开 http://127.0.0.1:8080 (FastAPI 托管 frontend/dist; 前端开发另开
# cd frontend && npm run dev 走 vite 代理)

# 注册项目 (wiki_project_id 是 llm-wiki 侧项目 id, 可与本系统 id 不同)
curl -X POST http://127.0.0.1:8080/projects -H 'Content-Type: application/json' \
  -d '{"id":"mae","path":"/path/to/project","wiki_project_id":"current",
       "rag_workdir":"/path/to/.lightrag-zh2"}'
```

### 方式 B: docker compose (P1c, 构建中)

```bash
cd deploy && cp .env.example .env   # 填 LLM_API_KEY
docker compose up -d                # crucible + postgres
# llm-wiki 暂为宿主机 19828, 容器内经 host.docker.internal 访问
```

### 核心 CLI (core 分支形态)

```bash
crucible query "那这个接口的鉴权是怎么做的" --history "MAE 有哪些外部接口？" --project-path <path>
crucible enum 组件 --alias-mode l2+l3 --project-path <path>
crucible experience "上传验证" --env staging --project-path <path>
```

## 环境变量 (统一 LLM, engineering-plan §3)

`deploy/.env` 是唯一事实源: `LLM_BASE` / `LLM_API_KEY` / `LLM_MODEL`,
各服务映射为自己的命名空间 (CRUCIBLE_LLM_* / LLM_BINDING_* / LLM_WIKI_LLM_*)。

其余见 `backend/app/config.py` (env 前缀 CRUCIBLE_)。

## 部署

完整指南: [docs/deploy.md](docs/deploy.md) (本机快速启动 / 服务器部署 / 模型补全 / 网络约定)。

## API 摘要

| 端点 | 说明 |
|---|---|
| `POST /fusion/query` | 融合查询 (query/history/env/alias_mode) |
| `POST /fusion/enum` | Q1 枚举 (M1 + L2/L3 对齐) |
| `POST /fusion/experience` | Q3 经验 (M3 状态排序 + 环境匹配) |
| `POST /projects` / `GET /projects` | 项目注册/列表 |
| `POST /projects/{id}/documents` | md/txt 上传 (双通道, 状态机) |
| `GET /health` | 进程/引擎健康探针 |

OpenAPI: `/docs`。
