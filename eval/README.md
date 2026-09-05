# 评测使用指导 (RAGAS)

## 1. 评测定位：三个层次，三种时机

| 层次 | 工具 | 测什么 | 何时跑 |
|---|---|---|---|
| **融合层** | `scripts/eval_ragas.py`（打 crucible API） | wiki+rag+M2 最终质量 | 每次策略调整后 / 周期回归 |
| **LightRAG 单引擎** | LightRAG 内置 `eval_rag_quality.py`（打 rag 端点） | 实体图检索+生成质量 | 换内网 LightRAG 版本前后 |
| **wiki 单引擎** | 暂无（同模式自写） | 页面检索质量基线 | 需要归因时 |

**归因原则**：融合分低时先看 rag 单引擎分——rag 低则查实体抽取/摄入，rag 高融合低则查 M2 比对/判别路由。

## 2. 黄金集建设流程（内网部署后）

```
第 1 天   内网部署完成 → 用现有 20 题跑一次 = 内网基线分
          (与本机分数对比, 定位环境差异)

第 2-3 周 正常使用, 零成本 —— query_logs 自动记录所有真实查询

第 3 周   从日志沉淀真实问题:
          python scripts/build_dataset_from_logs.py --top 50 --days 21
          → eval/pending_annotation.jsonl (按频次去重 + Q 类型分布)
          → 人工填 reference → 合并进 sample_dataset.jsonl

之后      每次大改动用黄金集回归
```

真实问题的价值 > 手编题：用户实际措辞、歧义、追问习惯只有日志知道。

## 3. 命令参考

### 融合层评测（本仓库）

```bash
# 基础用法 (LLM-only 指标: faithfulness + answer_relevancy)
python scripts/eval_ragas.py \
  --dataset eval/sample_dataset.jsonl \
  --project mae \
  --api http://127.0.0.1:8080

# 加 context 指标 (需本地 bge 嵌入, 更慢)
python scripts/eval_ragas.py --dataset eval/sample_dataset.jsonl --with-context

# 环境: LLM 走统一 .env (EVAL_LLM_BINDING_* 或 CRUCIBLE_LLM_*)
# 输出: 按 Q1/Q2/Q3 分组出分 (每组 ≥2 样本)
```

### 日志沉淀（第 3 周）

```bash
python scripts/build_dataset_from_logs.py --top 50 --days 21
# 输出 eval/pending_annotation.jsonl (reference 留空待标注)
```

### LightRAG 单引擎（内网版上线后, 在 LightRAG 机器上）

```bash
python lightrag/evaluation/eval_rag_quality.py \
  --dataset eval/sample_dataset.jsonl \
  --ragendpoint http://<内网lightrag>:9621
```

## 4. 使用时机清单

| 触发事件 | 跑哪层 | 判定 |
|---|---|---|
| 调判别规则/prompt | 融合层 | faithfulness 不掉 = 可合 |
| 换内网 LightRAG | rag 单引擎 + 融合层 | 双分不掉 = 替换成功 |
| 重摄入语料 | 融合层 | 对比摄入前后 |
| 新项目上线 | 融合层 (该项目) | 分数 ≥ 其他项目基线 |
| 每月一次 | 融合层 (黄金集) | 趋势记录 |

## 5. 依赖与坑（已踩平，勿改）

- ragas **0.3.1** + langchain 全家桶 0.3.x（ragas 0.4.x 与 langchain-community
  0.4.x 上游不兼容）
- DeepSeek 无 embeddings API → 必须显式传本地 bge 嵌入；脚本内强制 HF 离线
- 评测口径：枚举/对峙场景无 conclusion → 空答案记 0 分（已按 Q 类型分组，
  Q1 组不测 faithfulness 与 Q2/Q3 混比）

## 6. 基线记录

| 日期 | 环境 | 样本 | 结果 |
|---|---|---|---|
| 2026-09-04 | 本机 (arm64 模拟, 3 样本) | faithfulness 0.517 / answer_relevancy 0.935 | 口径未分流 |
| 2026-09-04 | 本机 (20 样本分组, 管道验收) | Q2: faithfulness 0.229 / answer_relevancy 0.727; Q1: NaN (枚举口径); 5 样本失败 (容器重启打断) | 低分主因: harness 只取 conclusion + station rag 仅 2 文档 + 模拟环境; 口径已修, 数字非质量判决 |
| 待内网 | 内网基线 | 20 题 | 部署后第 1 天跑 |
