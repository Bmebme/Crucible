# 统一知识框架与检索系统设计方案

**HieraExtract × LLM Wiki 融合方案**

> 版本: v1.3 · 日期: 2026-08-31 · 状态: 设计稿
> v1.1 变更: 新增 §2.4「链接机制对比」与 5 张架构图（`diagrams/`，SVG 自适应明暗主题）
> v1.2 变更: 新增 §4.6「融合层的 LLM 参与度与失真控制」
> v1.3 变更: 新增附录 C「融合索引构建管线」（两导出器伪代码、UNION SQL、增量重算规则）

---

## 1. 目标与定位

将两个互补的知识系统融合为一个统一的知识框架：

| 系统 | 知识形态 | 强项 | 短板 |
|------|---------|------|------|
| **HieraExtract** | 结构化实体 (L0~L4) + 关系图 | 实体精确、图遍历 (trace/impact/deps)、攻击面导出 | 无语义叙述、无长文上下文、纯业务维度 |
| **LLM Wiki** | 叙述性 Wiki 页面 + 原文溯源 | 语义叙述、演化合成、混合检索 (关键词+向量+图) | 实体非结构化、无法做精确依赖/影响面查询 |

**融合目标**：

1. **一份知识、两种视图** — 同一实体在结构化库 (可精确查询/遍历) 和 Wiki 页面 (可阅读/问答) 中都存在且互相链接。
2. **统一检索** — 一次查询同时召回结构化实体、叙述页面、图邻域、原始资料，融合排序后返回。
3. **双引擎生成** — 两个系统各自作为生成管道向统一数据库写入，互相触发、互相校验。
4. **服务化出口** — 统一 HTTP API + MCP 工具集，供 Claude Code、审计技能链 (`audit-report`/`unified-audit`)、其他 Agent 消费。

---

## 2. 现状盘点：两个子系统的能力边界

### 2.1 HieraExtract（结构化引擎）

- **知识模型**：L0 逻辑域 (`LogicalDomain`) → L1 系统架构 (`SystemArchitecture`: 子系统/外部接口/网络分区) → L2 域架构 (`DomainArchitecture`: 模块/公共机制) → L3 服务 (`Service`: 契约/数据所有权/技术栈) → L4 代码制品 (`CodeArtifact`)。
- **实体 ID**：确定性 `sha256(name)` 前缀 ID（`dom-`/`sub-`/`mod-`/`svc-`/`cmp-`），支持 `INSERT OR REPLACE` 幂等去重。
- **存储**：SQLite / PostgreSQL（JSONB 表 `logical_domains`/`services`/… + `entity_relations` + `documents` + `entity_embeddings`（pgvector, 512 维））。
- **查询能力**：`graph summary/trace/impact/deps/detail/mermaid`、`search_similar(query, layer)` 向量检索。
- **导出能力**：`audit_bridge.AttackSurfaceBuilder` → 端点清单/服务依赖图/外部暴露/技术栈矩阵。

### 2.2 LLM Wiki（叙述引擎）

- **三层模型**：raw sources（不可变原文）→ wiki（LLM 生成的 [[wikilink]] 互链页面，YAML frontmatter，`index.md`/`log.md`）→ schema（约定）。
- **三操作**：Ingest（两步思维链：先分析后生成）/ Query（带引用合成）/ Lint（矛盾检测、孤儿页、知识空白）。
- **检索**：混合检索 `search_project_inner` — 关键词 token 命中 + LanceDB 向量 + 一跳知识图谱候选，输出 `token_hits`/`vector_hits`/`graph_hits` 分区结果。
- **图谱**：四信号关联度（直接链接、来源重叠、Adamic-Adar、类型亲和）+ Louvain 社区；`graph/relevance.py` 提供 `RetrievalGraph.get_related_nodes()` 一跳扩展。
- **服务化**：`127.0.0.1:19828/api/v1`（search/graph/chat/files/reviews/sources/rescan）+ MCP Server（`llm_wiki_search`/`llm_wiki_graph`/`llm_wiki_chat`/`llm_wiki_read_file`…）。
- **Python 移植版**：`py-llm-wiki` 为 FastAPI 后端、契约与桌面版字节兼容，`backend/search/`、`backend/graph/`、`backend/chat/agent.py`（工具调用 Agent）均可直接复用。

### 2.3 互补性结论

| 能力 | HieraExtract | LLM Wiki |
|------|:---:|:---:|
| 精确实体查询（某服务的契约/技术栈） | ✅ | ⚠️ 需全文搜索 |
| 依赖链/影响面推导 | ✅ 图遍历 | ⚠️ 仅一跳 |
| 语义叙述（为什么这样设计） | ❌ | ✅ |
| 原始资料逐句溯源 | ✅ 文档级 | ✅ 页面级 |
| 持续演化（新文档增量更新） | ✅ 幂等 upsert | ✅ 增量缓存 |
| 攻击面导出 | ✅ | ❌ |

融合点即在于：**让结构实体可叙述、让叙述页面可结构化、让两条检索链路共享一个排序面。**

### 2.4 链接机制对比：声明式硬链接 vs 计算式软关联

两个系统的根本差异在**链接机制**上，理解这一点是融合层设计的前提：

**HieraExtract — 声明式硬链接**：

1. **身份硬**：实体 ID 是名字的确定性哈希（`sha256(name)` → `svc-a1b2c3d4e5`），同名即同实体，靠 `INSERT OR REPLACE` 幂等去重——身份一旦确定就钉死。
2. **关系硬**：边是显式声明的元组 `entity_relations(source_id, target_id, relation_type)`，LLM 从文档提取后落成固定 schema 的精确记录，不是推断的。
3. **分类硬**：L0~L4 五层是硬编码分类法，每个实体恰好属于一层、一张表。

→ 它的图是**声明式图**：边在入库时就定死了，`trace`/`impact` 遍历是精确推导，不涉及相似度计算。

**LLM Wiki — 计算式软关联**：

1. **链接软**：页面间的边是从正文中解析出来的 `[[wikilink]]`，没有独立边表——边随内容走。
2. **权重是算出来的**：四信号关联度（直接链接、来源重叠、Adamic-Adar、类型亲和）在查询时/建图时计算，Louvain 社区是算法发现的，没有任何声明。
3. **合并是语义级的**：新文档按 LLM 语义合并进已有页面（`page_merge.py`），不按 ID 幂等覆盖。

→ 它的图是**计算式图**：结构从文本派生，权重动态计算，关系是「相关度」而非「是/否」。

![图3 链接机制对比](diagrams/hard-vs-soft.svg)

**对融合设计的直接推论**：融合图谱必须同时容纳两类边（见 §4.3）——硬边保证精确性（影响面推导），软边补全语义召回（叙述上下文）。两者靠 `provenance` 区分来源、靠 `weight` 统一排序。

---

## 3. 总体架构

![图1 总体架构](diagrams/architecture.svg)

**设计原则**：

1. **不改动两个子系统的内核** — 融合层是增量层，两个引擎保持独立可运行。
2. **融合层无状态可重建** — 对齐映射、融合图谱都可由两个底层库重算（幂等重建脚本）。
3. **检索结果永远带溯源** — 每个命中项携带 `kb_id`/`page_path`/`source_doc` 三元组。
4. **写入即事件** — 任一引擎写入后发出事件，触发对端与融合层增量更新。

---

## 4. 知识融合层设计

### 4.1 统一知识模型（三层视图）

![图2 三层知识模型](diagrams/knowledge-layers.svg)

- **实体层**回答「**有什么、依赖谁、影响多大**」（精确推导，硬边）
- **叙述层**回答「**为什么、上下文、来龙去脉**」（语义阅读，软边）
- **原文层**提供「**证据**」（不可变，逐句可引）

### 4.2 实体-页面对齐映射表（核心数据结构）

融合层的第一张表：`kb_page_map`。

```sql
CREATE TABLE kb_page_map (
    kb_id        TEXT PRIMARY KEY,      -- hieraextract 确定性实体 ID, 如 svc-a1b2c3d4e5
    layer        INT  NOT NULL,         -- 0~4
    entity_name  TEXT NOT NULL,
    page_path    TEXT,                  -- wiki 页面相对路径, 如 wiki/services/订单服务.md
    page_title   TEXT,
    alias_count  INT DEFAULT 0,
    confidence   REAL DEFAULT 1.0,      -- 对齐置信度 (LLM 判定)
    source       TEXT DEFAULT 'manual', -- manual / llm_align / page_gen
    updated_at   TEXT
);
CREATE INDEX idx_kb_page_map_page ON kb_page_map(page_path);
CREATE INDEX idx_kb_page_map_name ON kb_page_map(entity_name);
```

**对齐算法（三条路径，按优先级）**：

1. **确定性锚点（零成本）**：Wiki 页面 frontmatter 声明 `kb_id` → 直接映射。
2. **LLM 对齐（批量）**：把 KB 实体清单（name + layer + definition）与 wiki 页面清单（title + 摘要）交给 LLM 做实体消解，输出 `(kb_id, page_path, confidence)`。低置信度进入人工确认队列。
3. **物化回填（自增长）**：没有对应页面的 KB 实体 → 由「实体页物化器」生成页面（见 4.4），生成后自动建立映射。

### 4.3 融合图谱

两个图取并集并加跨库边：

```
节点: KB 实体 (dom/sub/mod/svc/cmp) ∪ Wiki 页面
边:
  1. entity_relations (hieraextract, 带 relation_type/description)  ← 硬边
  2. wikilink 边 (llm_wiki, 权重=四信号关联度)                       ← 软边
  3. kb_page 边 (映射表物化, 权重=confidence)                        ← 跨库边
```

```sql
CREATE TABLE fusion_graph_nodes (
    node_id TEXT PRIMARY KEY,      -- kb_id 或 page_path (前缀 kb:/page: 区分)
    kind    TEXT NOT NULL,         -- entity / page
    label   TEXT NOT NULL,
    layer   INT,                   -- 仅 entity
    payload JSONB
);
CREATE TABLE fusion_graph_edges (
    source   TEXT NOT NULL,
    target   TEXT NOT NULL,
    rel_type TEXT NOT NULL,        -- depends_on / contract / wikilink / kb_page / ...
    weight   REAL DEFAULT 1.0,
    provenance TEXT,               -- hieraextract / llm_wiki / fusion
    PRIMARY KEY (source, target, rel_type)
);
```

**跨库遍历能力**（融合后新获得）：

- 从叙述页面进入结构图：`wiki 页面 → kb_page 边 → KB 实体 → entity_relations 全图 → impact 影响面`。
- 从结构实体进入叙述上下文：`KB 服务 → kb_page 边 → wiki 页面 → 一跳 wikilink → 相关方案/决策页`。
- 社区检测：在融合图上跑 Louvain（复用 llm_wiki 算法），跨库聚类（如「支付域实体 + 支付方案叙述页」成同一社区）。

### 4.4 实体页物化器（HieraExtract → LLM Wiki 方向）

把结构化实体编译为可读的 Wiki 页面，使 KB 在 Obsidian/llm_wiki 中可浏览：

```
输入: KnowledgeBase 中的一条实体 (如 Service)
输出: wiki/kb/<layer>/<name>.md
```

页面模板（frontmatter 承载结构化数据）：

```markdown
---
kb_id: svc-a1b2c3d4e5
kb_layer: 3
kb_type: Service
title: 订单服务
aliases: [order-service, 订单中心]
source_documents: [docs/api-spec.pdf, docs/deploy.md]
---

# 订单服务

## 定义
{{entity.definition}}

## 实现机制
{{entity.mechanism}}

## 接口契约
| method | path | auth |
...

## 依赖
- [[结算服务]]  ← wikilink 指向同域实体页
- [[消息队列#Kafka 主题]]

## 数据所有权
...

## 关联文档
- [[docs/api-spec.pdf]]
```

**触发与增量**：

- 触发时机：`hieraextract run` 完成 → 对比 `kb_page_map` 发现新实体 → 物化器生成页面。
- 更新时机：KB 实体 `updated_at` 变化 → 只更新变化字段段落（复用 llm_wiki 的 `page_merge.py` 合并语义）。
- 删除时机：实体被移除 → 页面标记 `kb_stale: true`，进入 Lint 队列而非直接删除（保护人工编辑）。

### 4.5 反向抽取（LLM Wiki → HieraExtract 方向）

叙述页面中若出现 KB 未覆盖的实体（如页面描述了一个新服务），反向抽取为候选实体：

- 抽取器：复用 hieraextract 的 L3/L4 提取 prompt，输入为单页 wiki 页面。
- 产出：候选 `Service`/`CodeArtifact` 实体，写入候选表，人工/自动确认后落库。
- 收益：KB 获得叙述层发现的新知识，wiki 保持唯一叙述入口。

---

## 4.6 融合层的 LLM 参与度与失真控制

### 4.6.1 组件参与度拆解

| 组件 | LLM 参与？ | 类型 | 是否写知识 |
|------|:---:|------|------|
| `kb_page_map` 路径1：frontmatter `kb_id` 锚点 | ❌ | 确定性 | 只建映射行 |
| `kb_page_map` 路径2：LLM 实体消解 | ⚠️ | 判别型 | 只建映射行 |
| `kb_page_map` 路径3：物化回填 | ❌ | 确定性（页面生成后自动建映射） | 只建映射行 |
| `fusion_graph` 构建 | ❌ | 纯图并集（读边表+解析 wikilink） | 只建图边 |
| 实体页物化器 | ⚠️ | 生成型 | 写叙述段落 |
| 反向抽取（wiki→KB 候选） | ⚠️ | 生成型 | 写候选表（需确认） |
| RRF 融合排序 | ❌ | 纯算术 | 不写任何东西 |
| 意图路由 | ⚠️（可纯规则） | 判别型 | 不写 |
| LLM 重排 top-30 | ⚠️（可选） | 判别型 | 不写，只排序 |
| 跨库 Lint | ⚠️ | 判别型 | 只出报告，不改 |

**核心结论：融合层的核心链路（映射、建图、融合排序）零 LLM。** LLM 只出现在两类位置——判别型的「选/排」与生成型的「写」。

### 4.6.2 两类错误：判别型配错对 vs 生成型转述走样

前两轮（hieraextract、llm_wiki 摄入）的失真是**生成型失真**：LLM 把原始文档压缩、改写、转述成实体或页面，信息被丢弃或走样，且不可逆。融合层引入 LLM 后，错误形态完全不同：

| 错误类型 | 形态 | 可逆性 | 污染面 |
|----------|------|--------|--------|
| 判别型错误 | 配错对（如实体对齐到错误页面、重排排序错误） | ✅ 一行表记录，改一行即修复 | 止于一条边，不产生新知识内容 |
| 生成型错误 | 转述走样（物化叙述曲解 KB 实体） | ⚠️ 需重新生成该段 | 渗入叙述段落 |

**设计原则：融合层只做「链接」，不做「重写」。** 凡是重写的位置必须带防护（见 4.6.3），使污染面止于一条边，而非像前两轮那样渗进知识内容本身。

### 4.6.3 生成型位置的防护

1. **结构化字段原样进 frontmatter，零改写** — `contracts`、`tech_stack`、`dependencies` 从 KB 逐字复制到页面 frontmatter，机器可校验、可 diff、不经过 LLM 转述。实体页的「真值」在 frontmatter，叙述只是人读的投影。
2. **页面是投影，不是新真理源** — KB 更新 → 物化器重新生成叙述段落（复用 `page_merge.py` 只覆盖变化段）。页面永远可从 KB 重建，失真上限是「一次生成周期」，无法累积。
3. **反向抽取只进候选表** — 候选实体不直接进 KB，人工/规则确认后才落库，天然隔离。
4. **融合层整体可幂等重建** — 任何融合操作出错，删掉融合层重算即可，前两轮的知识不受影响。这是与「生成型失真不可逆」的本质区别。

### 4.6.4 零 LLM 降级配置（失真敏感场景的默认态）

| 位置 | 零 LLM 替代 |
|------|------------|
| 对齐 | 名称规范化 + 别名表 + 确定性 ID 匹配先跑，LLM 只处理残差 |
| 物化器 | v1 纯模板渲染（Jinja），结构化字段直出 markdown；叙述段落后置为可选项 |
| 路由 | 关键词规则（影响/依赖/上游 → 图类型）足够分四类 |
| 重排 | 关闭，直接用 RRF 分数输出 |
| Lint | 矛盾检测先用字符串级 diff（KB definition vs 页面段落） |

LLM 只作为**增强项**逐步打开，每打开一个都配套置信度 + 人工队列。

### 4.6.5 融合层作为失真检测器

hieraextract 与 llm_wiki 是两次相互独立的提取，对同一份源文档。若融合时对同一实体给出矛盾描述（KB `definition` vs 实体页叙述、技术栈不一致），本身就是第一轮失真的信号。跨库 Lint 的矛盾检测（§5.3）把前两轮的失真**暴露出来**，而非增加新的。设计得当的融合层不追加失真，反而成为度量失真、回收失真的层。

---

## 5. 生成数据库设计

### 5.1 存储选型

| 组件 | 选型 | 理由 |
|------|------|------|
| KB 实体库 | PostgreSQL + pgvector | hieraextract 原生支持，多 Agent 并发 |
| Wiki 文件 | 文件系统（llm_wiki 项目目录格式） | Obsidian 兼容、人可读、git 可版本化 |
| 融合层 | PostgreSQL（与 KB 同库） | 融合图与实体图可 JOIN；单库备份 |
| 向量 | pgvector (实体) + LanceDB (wiki 页) | 保持两引擎原生能力，检索层统一调用 |

### 5.2 双管道写入流程

![图4 生成管道](diagrams/ingestion-pipeline.svg)

事件总线采用本地 SQLite 事件表（不引入 MQ）：

```
事件 A {type: kb_updated, entity_ids}   → 融合层: 重建受影响融合图边
                                         → 物化器: 新实体生成实体页 (写 wiki + log.md)
事件 B {type: wiki_updated, pages}      → 融合层: 重建受影响融合图边
                                         → 对齐器: 新页面跑 LLM 对齐 (kb_page_map)
```

### 5.3 一致性与 Lint 融合

扩展现有 Lint 操作为「跨库 Lint」：

| 检查项 | 说明 |
|--------|------|
| 矛盾检测 | KB 实体 `definition` vs 实体页叙述不一致 → 报告并建议保留较新来源 |
| 孤儿实体 | KB 实体无对应页面且从未被检索命中 → 提示物化或归档 |
| 陈旧页面 | 页面 `kb_stale: true` 超过 N 天 |
| 断链 | 实体页 wikilink 指向不存在的页面 |
| 对齐失效 | `kb_page_map` 中 page_path 已删除 |

---

## 6. 统一检索系统设计（核心）

### 6.1 四条召回通道

| 通道 | 引擎 | 能力 | 返回项 |
|------|------|------|--------|
| **R1 Wiki 混合召回** | llm_wiki `search_project_inner` | 关键词 token + LanceDB 向量 + 一跳图候选 | `token_hits`/`vector_hits`/`graph_hits` 页面 |
| **R2 实体向量召回** | hieraextract `search_similar` (pgvector) | 语义匹配实体（可 filter layer） | KB 实体 (id/name/layer/score) |
| **R3 图召回/遍历** | hieraextract `graph` + 融合图 | 依赖链、影响面、邻域扩展 | 实体路径 + 关联页面 |
| **R4 原文召回** | llm_wiki raw sources | 「只读原文」模式逐句引用 | 源文件片段 |

### 6.2 融合排序算法

![图5 检索流水线](diagrams/retrieval-pipeline.svg)

**RRF（倒数排名融合）**：

```
RRF(d) = Σ_c w_c / (k + rank_c(d))      k = 60 (标准值)
```

权重 `w_c` 按查询意图动态调整（见 6.3）。LLM 重排只对 RRF 后 top-30 执行，控制成本；重排 prompt 携带每条命中的「实体摘要/页面摘要 + 溯源」。

### 6.3 查询路由（意图分类）

| 查询类型 | 判别特征 | 权重策略 | 主导通道 |
|----------|---------|---------|---------|
| 实体精确查询 | 「订单服务的接口」「X 的技术栈」 | w2↑ w3↑ | R2+R3 → R1 补充叙述 |
| 依赖/影响面 | 「改 X 影响什么」「X 依赖链」 | w3↑↑ | R3 图遍历为主 |
| 语义叙述 | 「为什么这样设计」「方案对比」 | w1↑ w4↑ | R1+R4 |
| 混合综合 | 其余 | 均衡 | 四通道全开 |

判别器：轻量模型（DeepSeek）分类 + 关键词规则（影响/依赖/上游/下游 → 图类型）。

### 6.4 统一检索 API 契约

独立 FastAPI 服务（复用 `py-llm-wiki` 的代码组织方式），`127.0.0.1:19829/api/v1`：

```
POST /fusion/search
{
  "query": "订单服务的依赖链上有哪些数据存储？",
  "project": "xxx",               # 或 kb 库名
  "top_k": 10,
  "channels": ["r1","r2","r3","r4"],  # 可省略=自动路由
  "layer_filter": 3,               # 可选: 只看 L3
  "rerank": true
}
→ {
  "results": [
    {
      "rank": 1,
      "kind": "entity",            # entity / page / source
      "kb_id": "svc-a1b2c3d4e5",
      "name": "订单服务",
      "layer": 3,
      "page_path": "wiki/kb/3/订单服务.md",
      "score": 0.87,
      "snippet": "...",
      "provenance": ["r2", "r3"],   # 哪些通道召回
      "sources": ["docs/api-spec.pdf#p12"]
    }, ...
  ],
  "channels": {"r1": {...}, "r2": {...}},  # 各通道明细 (对齐 llm_wiki 风格)
  "routing": {"intent": "graph", "weights": {...}},
  "graph_context": {...}           # 可选: 融合图邻域, 供 Agent 继续遍历
}

GET /fusion/entity/{kb_id}          # 实体详情 + 关联页面 + 图邻域
GET /fusion/graph?node=xxx&depth=2  # 融合图遍历
POST /fusion/rebuild                # 重建融合层 (幂等)
```

### 6.5 MCP 工具集

在 llm_wiki MCP Server 模式上扩展（新 server 或同一 server 增工具）：

| 工具 | 说明 |
|------|------|
| `fusion_search` | 统一融合检索入口（上述 API 的封装） |
| `fusion_entity` | 按 kb_id/名称查实体详情（结构化字段+关联页面+依赖/被依赖） |
| `fusion_graph` | 融合图查询（邻域/trace/impact/deps 透传） |
| `fusion_align` | 手动对齐确认（低置信度队列处理） |
| `llm_wiki_*` 原有工具 | 保持兼容，继续可用 |

---

## 7. 与安全审计工作流的衔接

融合库对现有审计技能链的增益：

| 技能 | 融合前 | 融合后 |
|------|--------|--------|
| `audit-report` | 仅从 KB 导出攻击面 JSON | 攻击面 + 每项关联 wiki 叙述（认证机制沿革、已知设计权衡） |
| `code-audit` | 按代码审计 | 审计结论可回写 wiki「审计发现」页，进入统一检索 |
| `unified-audit` | 文档→KB→审计单向 | 闭环：审计发现 → wiki 页面 → 下次检索可见 |
| Chat Agent | 只能查 wiki 或只能查 KB | `fusion_search` 一次拿到结构+叙述+证据 |

---

## 8. 安全与权限

1. **服务仅绑定 127.0.0.1**，与 llm_wiki 相同的 token 认证模型（`LLM_WIKI_API_TOKEN` 或融合层自有 token）。
2. **敏感实体分级**：KB 实体 `metadata.sensitive: true` → 检索结果中该实体的契约细节只返回给 `audit` 角色；普通查询返回脱敏摘要。
3. **原文访问走 allow-list**：与 llm_wiki API 一致，路径白名单。
4. **审计日志**：融合检索请求（query + 返回实体清单）写入审计日志表，可追溯。

---

## 9. 实施路线图

### Phase 0 — 双引擎独立就绪（0.5 天）
- `hieraextract run` 与 llm_wiki/py-llm-wiki 分别跑通，同一批文档入库。
- 产物：两个可查询的知识库。

### Phase 1 — 融合层 MVP（2~3 天）
- 实现 `kb_page_map` 表 + LLM 批量对齐脚本 + 手动确认 CLI。
- 实现融合图谱构建（两库数据 → `fusion_graph_*` 表）与 `rebuild` 幂等脚本。
- 实现「实体页物化器」第一版（L3 Service → 实体页，含 frontmatter + wikilink 依赖）。

### Phase 2 — 统一检索（2~3 天）
- 融合检索服务：四通道并行召回 → RRF → LLM 重排 → 引用组装。
- 查询路由（意图分类 + 权重策略）。
- `fusion_search`/`fusion_entity`/`fusion_graph` MCP 工具。

### Phase 3 — 事件联动与闭环（1~2 天）
- 事件总线：KB 更新 → 物化器增量；wiki 更新 → 对齐器增量。
- 跨库 Lint 规则集。
- 审计技能链接入（`audit-report` 读取融合库）。

### Phase 4 — 评估与调优（持续）
- 检索质量基准（见第 10 节）。
- 反向抽取（wiki → KB 候选实体）。
- 敏感实体分级与权限。

---

## 10. 评估方案

### 10.1 检索质量基准

- **构建**：人工构造 30~50 条标注查询，覆盖四类意图（实体精确/依赖影响面/语义叙述/混合综合），标注期望命中实体+页面。
- **指标**：
  - `Recall@10`（期望实体/页面是否进入 top-10）
  - `MRR`（首个正确命中的倒数排名均值）
  - `通道贡献率`（R1~R4 各自贡献的正确命中占比，指导权重调参）
  - `溯源完整率`（top-5 结果带有效 source 的比例，目标 100%）
- **对比基线**：单用 llm_wiki 检索、单用 hieraextract 检索 vs 融合检索。

### 10.2 对齐质量

- LLM 批量对齐的精确率抽查（人工 20 条抽样验证 confidence 与事实一致性）。
- `kb_page_map` 覆盖率：KB 实体中已有页面映射的比例（目标 > 80%）。

### 10.3 一致性

- 跨库 Lint 假阳性率（每次 Lint 报告中人工确认的真实问题比例）。

---

## 附录 A：关键复用点（代码级）

| 融合组件 | 直接复用 |
|----------|---------|
| 融合检索服务框架 | `py-llm-wiki/backend/`（FastAPI、router 组织、SSE chat） |
| R1 通道 | `py-llm-wiki/backend/search/engine.py::search_project_inner` |
| R2/R3 通道 | `hieraextract/graph/query.py::GraphQueries` + `storage/db.py::search_similar` |
| 图邻域扩展 | `py-llm-wiki/backend/graph/relevance.py::get_related_nodes` |
| 实体页合并 | `py-llm-wiki/backend/wiki/page_merge.py` |
| 攻击面导出 | `hieraextract/audit_bridge.py::AttackSurfaceBuilder` |
| 事件源 | llm_wiki Source Watch + hieraextract `--dry-run` diff |

## 附录 B：技术栈矩阵

- 检索服务：Python 3.11+ / FastAPI / httpx
- 存储：PostgreSQL 15+（pgvector）/ SQLite（单机退化模式）/ LanceDB（wiki 向量）
- 对齐与重排 LLM：DeepSeek（判别/分类）+ GLM 5.1（重排与物化叙述）
- 事件：SQLite 事件表（本地轻量，无外部依赖）
- 服务化：HTTP API + MCP（stdio），与 llm_wiki MCP 同构

## 附录 C：融合索引构建管线（实现参考）

> 定位回顾：融合索引只服务 R3 图召回。R1/R2/R4 使用两侧原生索引（LanceDB / pgvector / 文件路径），不重建。
> 融合图是零 LLM、零取舍的并集投影，可随时幂等重建（§4.6）。

### C.1 导出器 A（hieraextract 侧）

不写裸 SQL，直接调用 hieraextract 自己的库——`system_architecture` 等容器表的展平由它完成，导出器只做搬运：

```python
from hieraextract.storage.db import open_db

async def export_kb(db_url: str):
    kb = await open_db(db_url).load()          # KnowledgeBase 已 flatten 全部五张表
    for dom in kb.logical_domains:             # L0
        yield Node("kb:" + dom.id, "entity", 0, dom.name, dom)
    for da in kb.domain_architectures:         # L2（含 modules）
        yield Node("kb:" + da.id, "entity", 2, da.domain_name, da)
    for svc in kb.services:                    # L3
        yield Node("kb:" + svc.id, "entity", 3, svc.name, svc)
    for cmp in kb.code_artifacts:              # L4
        yield Node("kb:" + cmp.id, "entity", 4, cmp.name, cmp)
    for r in kb.domain_relations + kb.component_relations:   # 硬边
        yield Edge("kb:" + r.source_id, "kb:" + r.target_id,
                   r.relation_type, 1.0, "hieraextract", r.description)
```

### C.2 导出器 B（llm_wiki 侧）

复用 py-llm-wiki 的库函数解析 wiki 目录，每页一个节点、每条 wikilink 一条软边：

```python
from backend.search.graph import build_graph    # py-llm-wiki 已有函数
from backend.wiki.frontmatter import parse_frontmatter

def export_wiki(project_path: str):
    nodes, edges = build_graph(project_path)    # 节点 + [[wikilink]] 边 + 权重
    for n in nodes:
        fm = parse_frontmatter(n["content"]).frontmatter or {}
        yield Node("page:" + n["id"], "page", None, n["title"],
                   payload={"path": n["id"], "frontmatter": fm})
    for e in edges:
        yield Edge("page:" + e["source"], "page:" + e["target"],
                   "wikilink", e.get("weight", 1.0), "llm_wiki")
```

### C.3 UNION 合并与索引

```sql
-- 节点: kb: / page: 前缀统一命名空间
CREATE TABLE fusion_graph_nodes (
    node_id TEXT PRIMARY KEY,
    kind    TEXT NOT NULL,          -- entity / page
    label   TEXT NOT NULL,
    layer   INT,                    -- 仅 entity
    payload JSONB NOT NULL,
    updated_at TIMESTAMPTZ DEFAULT now()
);

-- 边: 主键去重 (source,target,rel_type)，重复边不覆盖，provenance 保留来源
CREATE TABLE fusion_graph_edges (
    source     TEXT NOT NULL,
    target     TEXT NOT NULL,
    rel_type   TEXT NOT NULL,
    weight     REAL DEFAULT 1.0,
    provenance TEXT NOT NULL,       -- hieraextract / llm_wiki / fusion
    description TEXT DEFAULT '',
    PRIMARY KEY (source, target, rel_type)
);

-- 图遍历索引
CREATE INDEX idx_fge_source ON fusion_graph_edges(source);
CREATE INDEX idx_fge_target ON fusion_graph_edges(target);
CREATE INDEX idx_fgn_label  ON fusion_graph_nodes(label);

-- 跨库边: kb_page_map 表连接一步到位
INSERT INTO fusion_graph_edges (source, target, rel_type, weight, provenance)
SELECT 'kb:' || kb_id, 'page:' || page_path, 'kb_page', confidence, 'fusion'
FROM kb_page_map WHERE page_path IS NOT NULL;
```

### C.4 增量重算规则

事件驱动，只重算受影响行，不整体重建：

| 事件 | 载荷 | 重算动作 |
|------|------|---------|
| `kb_updated` | `entity_ids` | 重导这些实体节点 + `entity_relations` 中以其为 source 的边；旧边按 `entity_ids` 批量删除后重插 |
| `wiki_updated` | `pages` | 重导这些页面节点 + 其 wikilink 边（删除该页全部出边后重插） |
| `map_updated` | `(kb_id, page_path)` | 删除旧 kb_page 边 → 插入新边（一行级） |
| 全量重建 | — | `POST /fusion/rebuild`：TRUNCATE 两张表 → 跑 C.1+C.2+C.3，秒级完成 |

### C.5 v1 简化路径

- 融合图可先做成**内存缓存**（按项目，`POST /fusion/rebuild` 触发），不落 Postgres；验证 R3 效果后再落表。
- 导出器 A/B 各约 100 行，构建管线总代码量 ≈ 300 行。
