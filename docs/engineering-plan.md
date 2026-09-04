# Crucible 工程化方案 (engineering plan)

> 状态: P1 服务化 + P3a md/txt 上传 + P4a 前端已完成 (2026-09-03 夜)。
> 核心融合引擎已冻结在 `core` 分支 (`v0.1.0-core`, commit 47c7cd2)。
> 本文档记录工程化阶段的目标架构与决策, 实现细节决策点见 [decisions.md](decisions.md)。

## 0. 进度

| 项 | 状态 |
|---|---|
| P1a FastAPI + 引擎单例 | ✅ 实测 (enum 冷 10.6s → 热 5.6s) |
| P1b PG 元数据层 | ✅ projects/ingestion_jobs/query_logs (SQLAlchemy async) |
| P1c docker-compose + 统一 .env | ✅ 全栈容器实测: crucible+PG, wiki 走 host.docker.internal, HF/tiktoken 缓存绑定, 冷启动 6.7s; 项目路径在容器内为 /data/<name> |
| P3a md/txt 上传双通道 | ✅ 实测 (原子写入 + 状态机 done) |
| P4a 前端三页面 | ✅ Vue3+Element Plus, 构建产物由 FastAPI 托管 |
| P2 引用层 | ✅ Citation 契约 + 双引擎原文锚定 + M2 强制接地; 容器实测: M2 结论带 4 引用 (1 wiki 路径 + 3 rag 原文 chunk); 前端点击 wiki 引用弹整页原文抽屉 |
| P3 MinerU / P5 对齐管理台账 / P6 MCP+RAGAS+SSO | ⏳ 后续 |

> 运维注意: 本地开发与容器共享同一个 PG, 但项目路径命名空间不同
> (容器内 /data/<name>, 本地 /Users/...)。切换形态时需 UPDATE projects 表
> (或本地开发注册独立 project id)。

## 1. 目标形态

```
浏览器 ──┐
         ├─→ crucible (融合服务: FastAPI) ─┬─→ llm-wiki (19828, 页面引擎)
MCP/Agent┘                                  ├─→ lightrag (HTTP, 实体图引擎)
                                            └─→ postgres (元数据层)
mineru (文档→md) ──→ 双通道写入 (wiki 文件 + lightrag)
```

五个独立服务, docker-compose 编排, 全部容器化 (llm-wiki daemon 亦容器化,
与 rag 分离)。

## 2. 服务清单

| 服务 | 镜像来源 | 职责 | 关键点 |
|---|---|---|---|
| `crucible` | 自建 Dockerfile | 融合层: 判别/改写/合并/引用; 托管前端静态资源 | 引擎单例化 (LightRAG 模型加载 3.6s → 进程内一次) |
| `llm-wiki` | py-llm-wiki daemon 容器化 | 页面知识库后端 (19828) | headless 运行; 项目目录 volume; `allowLanAccess=true` 供 Tauri 桌面端连入; **补 env 覆盖 llmConfig** |
| `lightrag` | `lightrag-hku[api]` 官方 server | 实体图引擎 (demo 级) | 内网版上线后同契约替换, 只改地址 |
| `postgres` | postgres:16 | 元数据层 (见 §4) | volume 持久化 |
| `mineru` | `opendatalab/mineru` 官方镜像 | 二进制文档 → Markdown | CPU 起步; 仅 pdf/docx/ppt/图片走它 |

## 3. 统一 LLM 配置 (唯一 .env)

**所有引擎同一个 LLM**。`deploy/.env` 是唯一事实源, 各服务取自己的命名空间:

```env
# —— 统一 LLM (唯一一份) ——
LLM_BASE=https://api.deepseek.com/v1
LLM_API_KEY=sk-xxx
LLM_MODEL=<待定: llm_wiki 现用 deepseek-v4-pro, crucible 默认 deepseek-chat, 需统一>

# —— 各服务映射 ——
CRUCIBLE_LLM_BASE=${LLM_BASE}
CRUCIBLE_LLM_API_KEY=${LLM_API_KEY}
CRUCIBLE_LLM_MODEL=${LLM_MODEL}

LLM_BINDING=openai
LLM_BINDING_HOST=${LLM_BASE}
LLM_BINDING_API_KEY=${LLM_API_KEY}
LLM_MODEL=${LLM_MODEL}

LLM_WIKI_LLM_BASE=${LLM_BASE}
LLM_WIKI_LLM_API_KEY=${LLM_API_KEY}
LLM_WIKI_LLM_MODEL=${LLM_MODEL}

# —— 评测 (P6) ——
EVAL_LLM_BINDING_HOST=${LLM_BASE}
EVAL_LLM_BINDING_API_KEY=${LLM_API_KEY}
```

需要的跨仓库改动 (唯一一处): py-llm-wiki daemon 启动时读
`LLM_WIKI_LLM_*` env 覆盖 app-state.json 的 `llmConfig`。

嵌入模型 (bge-small-zh-v1.5) 不算 LLM, 独立配置; **若升级嵌入模型 (dim 变更),
LightRAG 索引必须全量重建 —— 上线前一次定稿**。

## 4. PostgreSQL 元数据层 (不是知识层)

知识本体留在两引擎各自存储 (wiki 文件 + LightRAG 图); PG 只存管理性数据:

| 表 | 内容 |
|---|---|
| `projects` | 项目注册与配置 (alias_mode/rag_language/guidance/aliases_file) |
| `ingestion_jobs` | 摄入任务状态机: 上传→MinerU→wiki 索引→rag 摄入, 逐步状态/耗时/错误 |
| `diff_ledger` | M1 差异清单持久化 + 收敛趋势 |
| `conflict_ledger` | M2 冲突对峙 + 裁决记录 (谁/何时/怎么裁) |
| `alias_reviews` | L3 审核队列: 等价对待确认 → 确认后回写 kb-aliases.yaml |
| `verify_records` | 验证记录索引 (verify_state/env/时间), 内容本体在 wiki 页面 |
| `query_logs` | 查询审计: query/Q 类型/模式/延迟/rewritten_to |
| `sessions` | 多轮会话历史 (MCP 接入时用) |

SQLAlchemy 2.0 async + asyncpg + Alembic。

## 5. 引用层 (Citation) —— 一等公民

现状: wiki 侧原文即页面 (天然锚定, 但 snippet 截 200 字符); rag 侧引擎内有
chunk 原文 + reference_id 但接口未暴露; provenance 是引擎级不是文段级。
LLM 评价指标 (faithfulness / citation recall/precision) 要求文段级引用。

契约: 每个结果项带
`citations[] = {source, path, chunk_id/reference_id, excerpt, heading_path}`

- wiki 通道: snippet 命中 → 整页原文 + 段落定位
- rag 通道: `include_references=True` → 解析 References → reference_id 反查 chunk 原文
- **M2 结论强制接地**: evidence 必带引用, 无引用不输出
- UI: 引用跳转原文 + 高亮
- P6 接 LightRAG 自带 RAGAS 评测 (Faithfulness/Answer Relevance/Context
  Recall/Context Precision), 自补 citation recall/precision

## 6. MinerU 摄入管线

```
上传 pdf/docx/ppt/图片 → mineru → 结构化 Markdown
上传 .md/.txt → 跳过 mineru
                  ↘ 双通道: <project>/wiki/ (llm-wiki 索引, daemon watcher)
                            + lightrag ainsert (实体图)
```

- 验证记录模板上传 (verify_state frontmatter) → Q3 数据入口
- 原子写入: 临时文件 + rename, 避免 watcher 竞态
- 已知边界: LightRAG 增量更新难 (ainsert 只增) — demo 级做"新文档插入 +
  手动重摄入"; 内网版由服务端管理
- 备选: Microsoft markitdown (轻量, 公式/表格弱), 接口留可替换位

## 7. UI (Vue 3 + Vite + Element Plus + Pinia, 前后端分离 monorepo)

```
crucible/
  backend/    FastAPI + SQLAlchemy + Alembic
  frontend/   Vue 3 + Vite + Element Plus
  deploy/     docker-compose + Dockerfile + .env
  src/        ← 融合核心 (core 分支内容)
```

页面:
1. **查询工作台**: 三面板 (results/differences/conflicts) + Q 类型徽章 + 溯源树
   + rewritten_to 展示 + alias_mode 切换 + **引用跳转原文高亮** + 导出
2. **项目中心**: 项目 CRUD + 配置管理 (alias_mode/guidance/别名文件)
3. **上传中心**: 拖拽批量、MinerU 进度、双通道索引状态可视化、验证记录模板
4. **对齐管理**: L3 审核队列 (等价对卡片: 接受→回写词典/拒绝) +
   kb-aliases.yaml 可视化编辑器 + 词典命中统计
5. **台账**: 差异/冲突浏览与裁决 + 差异收敛趋势图
6. **实体图浏览** (P6): LightRAG graphml 可视化

## 8. 阶段表

| 阶段 | 内容 |
|---|---|
| **P1** | Docker 五服务骨架 + .env 统一 LLM + FastAPI + PG 模型 + 引擎单例化 (rag_engine 改 HTTP 客户端指向 lightrag 服务) + py-llm-wiki env 覆盖补丁 |
| **P2** | 引用层: Citation 契约 + 双引擎原文锚定 + M2 强制接地 (现有语料即可做) |
| **P3** | MinerU 服务 + 上传/摄入管线 + ingestion_jobs 状态机 |
| **P4** | 前端: 查询工作台 (含引用跳转) + 上传中心 |
| **P5** | 对齐管理 + 台账 + L3 审核队列闭环 |
| **P6** | MCP 工具集 + RAGAS 评测接入 + 内网 LightRAG 替换 + SSO + 实体图浏览 |

## 9. 已拍板决策

- 全部容器化, llm-wiki 与 rag 分离为独立服务
- PostgreSQL 做元数据层 (不是知识层)
- MinerU 前置做文档 md 化 (LightRAG ainsert 只吃纯文本, 确认)
- UI 用正经前端工程 (Vue 3 + Element Plus), 不做简单页面
- 统一 LLM 配置: 唯一 .env, 所有引擎同一个 LLM

## 10. 待定项

1. **统一 LLM 模型值**: llm_wiki 现用 `deepseek-v4-pro`, crucible 默认
   `deepseek-chat` — 部署时定一个
2. 备份策略: PG dump + 项目目录打包 (wiki 文件 + .lightrag), 频率待定
3. 认证: 一版 token/API key 门, SSO 后置 (P6)
4. 分支策略: core = 归档基线 + tag, 主线自由演进 (融合核心改动不自动回灌 core,
   按需 cherry-pick)
