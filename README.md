# Crucible 融合检索层

llm_wiki 页面引擎 × LightRAG 实体图引擎的**判别、召回与合并**工程实现。
设计依据: [DESIGN-search-strategy.md](../knowledge-fusion/DESIGN-search-strategy.md)。

## 架构

```
query
  → classifier      判别器: 规则 + LLM 三分类 (Q1 枚举·全 / Q2 机制·准 / Q3 经验·可信)
  → engines         双引擎并行召回
       wiki_engine  llm_wiki HTTP API (混合检索 / 页面清单 / frontmatter)
       rag_engine   LightRAG (实体枚举 / hybrid 查询, demo 级适配)
  → merge           按类型合并
       m1_union         Q1: 并集 + 差异清单 (知识缺口信号)
       m2_consistency   Q2: LLM 一致性比对 (只比对不重写, 冲突对峙不裁决)
       m3_state         Q3: verify_state 加权排序 (原样引用)
  → orchestrator    编排 + 统一响应 (结果 + 溯源 + 冲突/差异)
  → cli             crucible query / enum / experience
```

## 安装

```bash
pip install -e ".[rag,dev]"     # rag extra 含 lightrag-hku + sentence-transformers
```

环境变量:

| 变量 | 默认 | 说明 |
|------|------|------|
| `CRUCIBLE_WIKI_BASE` | http://127.0.0.1:19828 | llm_wiki 后端 |
| `CRUCIBLE_RAG_WORKDIR` | `<project>/.lightrag` | LightRAG 工作目录 |
| `CRUCIBLE_LLM_BASE` | https://api.deepseek.com | LLM 端点 |
| `CRUCIBLE_LLM_API_KEY` | — | LLM Key |
| `CRUCIBLE_LLM_MODEL` | deepseek-chat | LLM 模型 |
| `CRUCIBLE_EMBED_MODEL` | BAAI/bge-small-zh-v1.5 | 本地嵌入模型 |

## 用法

```bash
# Q1 枚举 (M1 并集合并, 输出差异清单)
crucible enum 组件 --project-id current --project-path /path/to/project

# 通用融合查询 (判别 → 召回 → 合并)
crucible query "MAE 的 hiro 总线是什么？" --project-path /path/to/project

# Q3 经验 (M3 verify_state 加权)
crucible experience "上传验证" --project-path /path/to/project --env staging
```

## 测试

```bash
pytest tests/
```

## 与设计文档的映射

| 文档章节 | 实现 |
|---------|------|
| §2.1 三类查询 | `schemas.QueryType` |
| §2.2 判别器 | `classifier.py` (规则表 + LLM prompt + <0.7 保守策略) |
| §5.1 M1 | `merge/m1_union.py` (并集 + 差异 → 回填建议) |
| §5.2 M2 | `merge/m2_consistency.py` (一致/冲突对峙, 逐条引用) |
| §5.3 M3 | `merge/m3_state.py` (权重 1.0/0.5/0.2/0.0) |
| §5.4 路径归 Agent | 编排器无路径合成; Q2 召回供 Agent 组合 |
| §6 三步路由 | `orchestrator.py` 三类执行路径 |

## 待补充 (后续)

- FastAPI 服务化 (`/fusion/search` 出口)
- MCP 工具集 (kb_scenario / kb_goal / kb_poc / kb_record_verification)
- LightRAG 内网 demo 版本替换 `rag_engine.py` 适配层
