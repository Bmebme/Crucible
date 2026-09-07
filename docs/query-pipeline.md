# Crucible 查询管线流程图 (Q1/Q2/Q3 判别 → 检索 → 合并)

```mermaid
flowchart TD
    U["用户查询 (多轮: 先指代消解/省略补全, LLM 改写失败原样降级)"] --> C{判别 classify<br/>两段式}

    C -->|"① 关键词规则表命中<br/>(毫秒, confidence=1.0)"| ROUTE
    C -->|"② 规则未中 → LLM 判别<br/>(响应 JSON, conf<0.7 不采纳)"| ROUTE
    C -->|"③ 都失败 → 保守兜底<br/>默认 Q2 + 全通道, confidence=0"| ROUTE

    ROUTE{"按类型分派"} --> Q1["Q1 枚举型 (全)"]
    ROUTE --> Q2["Q2 机制型 (准)"]
    ROUTE --> Q3["Q3 经验型 (可信)"]

    subgraph Q1S[" "]
        Q1 --> R1["召回: wiki 关键词 (limit=100) ∥<br/>rag 实体枚举 (LightRAG local 模式)"]
        R1 --> M1["M1 并集合并<br/>+ L2 别名词典 (kb-aliases.yaml)<br/>+ L3 LLM 批量别名消解 (候选剪枝, 低置信不采纳)"]
        M1 --> O1["输出: 目录式清单<br/>(条目 + 完整句子简介)<br/>软隔离: related_projects 联邦检索 weight 0.1"]
    end

    subgraph Q2S[" "]
        Q2 --> R2["并行召回:<br/>① wiki 关键词搜索 (limit=3)<br/>② rag hybrid 查询<br/>③ rag 原文 chunk 上下文<br/>④ llm-wiki chat 参考回答"]
        R2 --> M2{"M2 一致性比对 (LLM)<br/>chat 回答作叙述底稿,<br/>事实必须被双引擎证据支撑"}
        M2 -->|"一致"| O2A["合并结论:<br/>完整段落回答 + 双引擎证据 + 引用<br/>(强制接地: 无引用不输出合并结论)"]
        M2 -->|"冲突"| O2B["对峙输出: 双方说法 + 证据<br/>(不裁决, 由 Agent/人裁决后回写)"]
    end

    subgraph Q3S[" "]
        Q3 --> R3["召回: wiki 验证记录搜索<br/>(verification 目录, 读 verify_state frontmatter)"]
        R3 --> M3["M3 状态加权排序:<br/>成功 > 未验证 > 拦截(负知识)<br/>blocked 仅环境匹配时返回"]
        M3 --> O3["输出: 验证记录清单 (weighted)"]
    end

    O1 --> F["统一响应: 分段耗时 + 引用(句界 excerpt)<br/>+ wiki 原文块 + RAG 清洗正文块"]
    O2A --> F
    O2B --> F
    O3 --> F
```

## 关键设计点

| 环节 | 技术细节 |
|---|---|
| 判别两段式 | 规则正则先行（高频句式约七成, 零 LLM）→ LLM 兜底（低置信不采纳）→ 保守策略（默认 Q2 全通道, 判不准不硬判） |
| 检索通道 | wiki = 关键词+图（llm-wiki search.rs port, 向量腿待接通）；rag = 向量+实体图（LightRAG）；chat 参考 = llm-wiki 完整回答作叙述底稿 |
| M1 并集 | 名字归一化 + L2 词典（人工确认等价）+ L3 LLM 批量判定（候选剪枝 ≤40 对, confidence≥0.7 才采纳） |
| M2 一致性 | 三输入（wiki 结论 + rag 结论 + chat 底稿）→ LLM 只比对不重写；一致才出合并结论且强制带引用（宁缺毋滥守 faithfulness）；冲突不裁决 |
| M3 状态排序 | verify_state 四态加权（success/blocked/unverified/false_positive），blocked 只在环境匹配时呈现 |
| 软隔离 | 项目默认隔离；related_projects 声明后联邦检索 weight 0.1，只进参考区不进主结果 |
| 引用层 | wiki 引用 = 整页原文句界 excerpt（剥 frontmatter）；rag 引用 = chunk 清洗正文；所有结论带来源，无来源降级 unverified |
