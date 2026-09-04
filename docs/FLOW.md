# Crucible 全流程指南

> 一页看懂整个系统: 从部署到日常使用。深入细节见文末「文档索引」。

## 0. 这是什么

一个**漏洞验证知识库融合检索系统**：把两份知识源（llm-wiki 页面库 + LightRAG
实体图）融合成一个查询入口，服务漏洞验证 Agent 的三步工作流（场景构建 →
路径规划 → POC 验证）。

```
问题进来
  → 判别器: 自动分三类 (Q1 枚举·全 / Q2 机制·准 / Q3 经验·可信)
  → 双引擎召回: llm-wiki 页面 + LightRAG 实体图 (每个答案带原文引用)
  → 合并: M1 并集+差异清单 / M2 一致性比对 / M3 验证状态排序
  → 输出: 结果 + 溯源 + 引用 + 差异/冲突清单
```

## 1. 部署（一条线）

```bash
git clone https://github.com/Bmebme/Crucible.git
cd Crucible/deploy
./bootstrap.sh            # 生成 .env → 填入 LLM_API_KEY → 再跑一次
```

bootstrap.sh 自动做 6 件事：检查 .env → clone py-llm-wiki（同级）→ 基础镜像
绕 Docker Hub → 构建 → 启动 → 健康检查。

**两个可选档位**：

| 档位 | 命令 | 说明 |
|---|---|---|
| 默认 | `./bootstrap.sh` | docreader 用 markitdown 轻量解析（构建快） |
| 全量 | 见 deploy.md §3.3 生成模型镜像后 `--profile full` | MinerU 高质量解析 + 容器化 llm-wiki |

**五个服务**：crucible(8080 唯一入口) + postgres(元数据) + docreader(文档解析)
+ llm-wiki(页面库, profile full) + LightRAG(进程内, 数据在项目目录)。

## 2. 第一次使用（一条线）

```bash
# 1. 注册项目 (容器内路径 = /data/<项目名>, DATA_ROOT 决定)
curl -X POST http://127.0.0.1:8080/projects -H 'Content-Type: application/json' \
  -d '{"id":"mae","path":"/data/mae","wiki_project_id":"current",
       "rag_workdir":"/data/mae/.lightrag-zh2"}'

# 2. 打开 http://127.0.0.1:8080
```

页面七个：**查询工作台**（输入即预判 Q 类型）→ **上传中心**（md/txt 直传,
pdf/docx 走 docreader）→ **知识台账**（差异/冲突裁决）→ **对齐管理**（L3 审核
队列 → 确认回写词典）→ **实体图**（LightRAG 图浏览）→ **项目中心**。

## 3. 日常操作手册

| 想做什么 | 怎么做 |
|---|---|
| 查组件清单 | 查询工作台选「枚举」输「组件」→ M1 并集 + 差异清单 |
| 查机制细节 | 「融合查询」输「X 的鉴权是怎么做的」→ M2 结论带引用, 点引用看原文 |
| 查历史验证 | 「经验查询」输「上传验证」+ 环境名 → M3 按 verify_state 排序 |
| 多轮追问 | 历史框里填前几轮提问 → 「那这个接口呢」自动消解 |
| 上传文档 | 上传中心拖拽; 验证记录选 verification 子目录 (带 verify_state frontmatter) |
| 处理 L3 判定 | 对齐管理 → 确认等价（自动写进 kb-aliases.yaml）/ 拒绝 |
| 裁决冲突 | 知识台账 → 采信 wiki / rag / 都对 / 都不对 → 记录留痕 |
| 换对齐策略 | 查询时选 alias_mode: l2+l3（词典+LLM）/ l3（纯LLM）/ off（关闭） |
| 重新摄入 | 上传中心重传; 或删除 .lightrag-* 目录后重新 ingest |
| 跑评测 | `python scripts/eval_ragas.py --dataset eval/sample_dataset.jsonl` |
| Agent 接入 | MCP server: `backend/mcp_server.py`（4 个工具, 描述自带使用阶梯） |

## 4. 概念速查

- **Q1/Q2/Q3**：枚举（要全）/ 机制（要准）/ 经验（要可信）——判别器自动分, 规则命中可见
- **M1/M2/M3**：按 Q 类型选的合并模式; M2 只比对不裁决, 冲突对峙交人
- **L2/L3**：名字对齐两层——词典（人工确认, 零成本）→ LLM 消解（兜底, 确认后回写词典）
- **verify_state 四态**：verified_success / unverified / verified_blocked（环境匹配才返回）/ false_positive（仅误报排查返回）
- **引用层**：每个结果带文段级 citations, M2 结论无引用不输出（faithfulness 底线）
- **知识分层**：KB 只枚举+带引用, 推断（路径规划/裁决）在 Agent/人

## 5. 目录结构

```
src/crucible/     核心融合层 (core 分支归档 v0.1.0-core)
backend/          FastAPI 服务 + PG 元数据 + 上传管线 + MCP server
frontend/         Vue3 前端 (7 页面)
docreader/        文档解析服务 (MinerU/markitdown)
deploy/           compose + bootstrap.sh + Dockerfile + .env (统一 LLM)
scripts/          评测脚本 + MinerU 模型补全脚本
eval/             评测数据集 + 基线记录
docs/             设计文档 + 决策记录 + 工程计划 + 部署指南 + 本文
```

## 6. 常见问题

| 症状 | 原因/处理 |
|---|---|
| 查询空结果 | 项目路径命名空间错了（容器内 /data/... vs 本地宿主路径）→ 检查 projects 表 |
| docreader 解析失败 | 看 `docker logs deploy-docreader-1`: 模型缺失跑 fetch_mineru_models.py; OOM 换小文件 |
| 首次查询慢 (~10s) | LightRAG 模型加载, 之后热缓存 ~5s, 正常 |
| M2 没有结论 | 无引用支撑强制降级或 LLM 不可用, 响应 notes 有说明 |
| GitHub 推送失败 | 间歇阻断, 重试即可 |

## 7. 文档索引

| 文档 | 内容 |
|---|---|
| [README.md](../README.md) | 入门 + API 摘要 |
| [deploy.md](deploy.md) | 部署细节: 服务器步骤/模型补全/网络约定/待办 |
| [engineering-plan.md](engineering-plan.md) | 工程化目标架构 + 进度表 |
| [decisions.md](decisions.md) | 13+ 条关键决策: 背景/决策/实现位置/调整入口（调策略先看这里） |
| [DESIGN-search-strategy.md](../../knowledge-fusion/DESIGN-search-strategy.md) | 设计原理（知识融合理念, 在 knowledge-fusion 仓库） |
