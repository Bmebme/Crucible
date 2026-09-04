# 决策记录 (ADR)

本文记录融合层的关键策略决策点，用于**后续调整策略时快速定位改哪里**。
设计文档（`DESIGN-search-strategy.md` §5）讲"为什么"，本文讲"代码怎么落地、
调整入口在哪"。每个决策点四段：

- **背景** — 要解决的问题（含已知痛点/实测数据）
- **决策** — 选了哪种做法、为什么
- **实现** — 文件、函数、常量（调整时改这里）
- **调整** — 以后换策略时的入口与注意点

---

## D-01 查询判别：Q1/Q2/Q3 三分类

**背景**：漏洞验证 Agent 的知识需求有三类不同指标——枚举要全（召回率）、机制要准
（精确率）、经验要可信（采纳率）。一种检索参数伺候不了三种需求（§2.1）。

**决策**：规则表先行（覆盖高频句式，零成本）+ LLM 兜底（OpenAI 兼容端点）+ 保守
策略（confidence < 0.7 或 LLM 不可用 → 全通道召回，由合并层按结果形态决定）。

**实现**：
- `src/crucible/classifier.py` `_RULES` — 规则表（顺序即优先级：
  ENUM → EXPERIENCE → MECHANISM；"以前"必须排在"怎么"前）
- 同文件 `_CHANNELS`（类型→召回通道）、`_MERGE_MODE`（类型→M1/M2/M3）、
  `_LLM_PROMPT`（兜底分类 prompt）、`classify()`（两段式入口）

**调整**：新增高频句式 → 加 `_RULES` 正则（注意优先级顺序，加完跑
`tests/test_classifier.py`）；LLM 兜底行为 → 改 `_LLM_PROMPT`。

---

## D-02 多轮追问改写：指代消解 + 省略补全

**背景**：Agent 多轮交互会有"那这个接口呢"这类追问，"这个"无历史无法消解，
原样进检索两个引擎都搜不准。WeKnora 的 QUERY_UNDERSTAND 用一次 LLM 调用做
改写+意图（§10.2 采纳项）。

**决策**：改写与判别**分离**——先检测（纯正则，零成本）后改写（仅追问+有历史
时触发 LLM），判别仍走 D-01 的两段式（规则表优先，改写后的查询含具体词更能
命中规则）。改写失败/无 LLM → 原样降级。

**实现**：
- `src/crucible/classifier.py` `_FOLLOWUP_RES`（指代锚点：那[个些]?/这[个些]?/
  它/其/上述/前面/刚才…）、`_SHORT_ELLIPSIS_RE` + `_QUERY_LEN_MAX=12`
  （短问句以 呢/吗 结尾视为省略追问）、`_is_followup()`、
  `_REWRITE_PROMPT`、`_rewrite_by_llm()`、`rewrite_if_needed()`
- `src/crucible/orchestrator.py` `run(history=...)` — 改写先行，
  判别与双引擎**全部**用消解后的查询；响应保留原 query + `rewritten_to` 溯源

**调整**：追问检测太松/太紧 → 改 `_FOLLOWUP_RES`、`_QUERY_LEN_MAX`；
改写 prompt 质量 → 改 `_REWRITE_PROMPT`；history 轮数 → `_rewrite_by_llm`
里的 `history[-6:]`。

---

## D-03 双引擎分工：wiki 页面引擎 + LightRAG 实体图

**背景**：llm_wiki 是页面级知识库（结构页、混合检索、frontmatter 状态）；
hieraextract 是表结构抽取，已被定位为过渡方案。需要第二个引擎补"关系/实体"
视角。

**决策**：wiki 页面引擎 + LightRAG 实体图引擎（demo 级适配），**不引入
hieraextract**。wiki 负责结构枚举与页面检索（Q1 的清单来源、M3 的
verify_state 载体）；LightRAG 负责实体枚举与多跳/机制查询（Q1 的实体补集、
Q2 的第二证据源）。两者保持**查询时融合**，无离线合并索引（§9.1）。

**实现**：
- `src/crucible/engines/wiki_engine.py` — search / list_pages /
  read_page_frontmatter（19828 契约）
- `src/crucible/engines/rag_engine.py` — enumerate_entities / query / ingest
  （LightRAG 1.5.7，内网 demo 上线后按同样接口替换实现）

**调整**：替换 LightRAG 后端 → 只动 `rag_engine.py`（接口不变）；
wiki API 契约变化 → 只动 `wiki_engine.py`。

---

## D-04 合并模式按 Q 类型选择，拒绝权重融合

**背景**：WeKnora 用 RRF 静态权重（0.7/0.3）融合，意图只做门控不做权重适配
（源码实证）。三种查询类型需要的是**不同的合并逻辑**（并集/一致性/状态排序），
不是同一个公式的不同权重。

**决策**：按 Q 类型选合并模式——Q1→M1 并集、Q2→M2 一致性比对、Q3→M3 状态排序。
不引入 RRF/权重调参。

**实现**：`src/crucible/classifier.py` `_MERGE_MODE` 映射表；
`src/crucible/orchestrator.py` `run()` 按类型分发 `_run_enum` /
`_run_experience` / `_run_mechanism`。

**调整**：新增查询类型或换合并策略 → 改 `_MERGE_MODE` + orchestrator 分发。

---

## D-05 M1 名字归一化与差异清单

**背景**：wiki 给的是页面相对路径（`concepts/代码制品l4`），rag 给的是实体名
（`Code Artifact`）。直接字符串比对永不命中。差异清单本身是产物：两引擎独立
提取，不一致处即可疑点（知识缺口信号，不是错误）。

**决策**：归一化到"最后一节、NFKC、小写、空白折叠"后比对；同名命中合并，
差异按来源标注并附回填建议（wiki 侧缺失 → "回填 wiki 清单页"，rag 侧缺失 →
"检查 rag 抽取为何遗漏"）。wiki 通道按 hint **检索相关页**而非全量清单
(实测: 全量清单让"外部接口"并集=190 项全库倾倒; 检索后 wiki 侧收窄到
相关 20 页), 过滤 index/log/overview 导航页, 检索无结果才退全量降级。
条目携带简介 (wiki snippet / rag description), 引用段落级定位见 D-06 附注。

**实现**：`src/crucible/merge/m1_union.py`
- `normalize_name()` — 去 .md、去路径前缀（rsplit "/"）、NFKC、lower、空白折叠
- `union_merge()` — 归一化命中即合并，差异生成 `Difference`

**调整**：改名归一化规则 → 改 `normalize_name()`（测试在
`tests/test_m1_union.py`）。**已知边界**：连字符 vs 驼峰（`hiro-路由总线` vs
`Hiro路由总线`）、复合名拆分（`er-ir-接口分类` vs `er接口`+`ir接口`）归一化
解决不了，是 D-08 L2 词典的输入；差异清单就是 L2 词条的挖掘源。

---

## D-06 M2 一致性比对：LLM 只比对不重写

**背景**：Q2 机制型要"准"。两引擎各给一个机制结论，直接拼接会互相污染。
需要第三方判断一致/冲突，但 LLM 重写会引入二次失真。

**决策**：LLM 只做一致/冲突判断，不重写证据；一致 → 合并结论（high 置信 +
三源溯源）；冲突 → 对峙输出，**不裁决**，由 Agent/人裁决后回写（§8.2 闭环）。
LLM 不可用 → 降级为单源并列（confidence=degraded）。

**实现**：`src/crucible/merge/m2_consistency.py` `compare_mechanism()`；
`src/crucible/orchestrator.py` `_run_mechanism()`（wiki 取 top1 snippet +
rag 取 hybrid 回答前 600 字喂比对）。

**调整**：比对 prompt → 改 m2_consistency 里的 prompt；召回条数 → 改
`_run_mechanism` 里 `wiki.search(limit=3)`；冲突处理策略（比如自动裁决）→
改 `_run_mechanism` 的 else 分支。

**引用段落定位**：wiki 顶部引用自动补 `heading_path`
(`wiki_engine.find_heading`: 在整页原文中定位 snippet 最近标题),
调整锚点粒度改该函数。

---

## D-07 M3 verify_state 加权：状态不合并内容

**背景**：Q3 经验型要"可信"。历史验证记录的价值取决于其验证状态（成功/未验证/
被拦/误报），且与**当前环境**相关。

**决策**：历史结果原样引用，只按 frontmatter 的 `verify_state` 加权排序：
verified_success=1.0 / unverified=0.5 / verified_blocked=0.2（环境不符则 0，
仅环境匹配时作为负知识返回）/ false_positive=0（仅误报排查时返回）。

**实现**：`src/crucible/merge/m3_state.py` `_WEIGHTS` 权重表、
`sort_by_verify_state()`；状态来源：wiki 页面 frontmatter（
`wiki_engine.read_page_frontmatter`）。

**调整**：改权重 → 改 `_WEIGHTS`（测试在 `tests/test_m3_state.py`）；
环境匹配逻辑 → 改 `sort_by_verify_state` 的 blocked 分支。

---

## D-08 跨语言对齐：L1/L2/L3 三层（§5.5）

**背景**：内部文档全是中文，而 LightRAG 默认提取英文实体（DEFAULT_SUMMARY_
LANGUAGE="English"），与中文 wiki 页面名无法比对（实测 union=15 时大量假差异）。

**决策**：
- **L1（必做，已实现）**：LightRAG 提取中文实体
  `addon_params={"language": "Simplified Chinese"}`（实测生效：实体名与类型词表
  均为中文）
- **L2（已实现）**：`kb-aliases.yaml` 别名词典——复合名拆分（`er-ir-接口分类`
  ↔ `er接口`+`ir接口`）、等价名组（`hiro-路由总线` ↔ `Hiro总线`）。词条来源：
  M1 差异清单手工收敛，**只收录确认过的等价关系**
- **L3（已实现）**：LLM 实体消解——L2 未命中的候选对（表面相似性剪枝后）
  一次批量调用判定等价，confidence ≥ 0.7 才采纳；判定结果经人工确认后
  **回写 L2 词典**（闭环）

**开关**：`alias_mode`（env `CRUCIBLE_ALIAS_MODE` / CLI `--alias-mode`）：
- `l2+l3`（默认）词典先行，未命中走 LLM
- `l3` 跳过词典，全部走 LLM 消解
- `off` 关闭对齐，纯归一化比对（基线）

实测（mae, enum 组件）：off union=189/diff=184 → l3 union=185/diff=175 →
l2+l3 union=186/diff=178。

**实现**：L1 在 `src/crucible/rag_engine.py` `addon_params` +
`config.py` `rag_language`；L2/L3 在 `src/crucible/merge/aliases.py`
（`load_alias_dict`/`candidate_pairs`/`resolve_llm_pairs`），`union_merge`
比对前调用（`aliases`/`llm_same` 参数），orchestrator `_run_enum` 编排
（两遍合并：第一遍拿剩余差异 → 候选剪枝 → LLM 判定 → 第二遍正式合并）；
词典文件在项目根 `kb-aliases.yaml`（`config.aliases_file`）。

**调整**：候选剪枝启发式 → `candidate_pairs` 的 `sim_score`/`_TOKEN_MIN_LEN`；
LLM 判定 prompt/置信阈值 → `_L3_PROMPT`/`_L3_CONFIDENCE_MIN`；
批量上限 → `_L3_BATCH_MAX`。

---

## D-09 实体噪声治理：三层防线

**背景**：LightRAG 抽取实测噪声：JSON 字段名（name/type/role）、日期（2025.1）、
版本号（3.2.1）、规格（32C128G）、模板泄漏（`ENTITY_START|…`）。噪声直接污染
M1 差异清单。

**决策**：源头约束 + 客户端兜底。
1. **提取期（源头）**：`addon_params={"entity_types_guidance": …}` —— 约束
   抽取类型、排除字段名/日期/版本（WeKnora 固定类型+自定义指令思路，1.5.7
   实测生效：类型词表从通用英语类型变为领域化中文类型）
2. **查询期（兜底）**：名称模式黑名单（日期/版本/数值/模板泄漏）+ 名称精确
   黑名单（常见字段名）+ 类型黑名单（`其他` 类全为字段名噪声）

**实现**：`src/crucible/rag_engine.py` `_NOISE_RES`、`_NOISE_NAMES`、
`_NOISE_TYPES`、`_is_noise()`；guidance 默认文本在 `config.py`
`rag_entity_guidance`（env `CRUCIBLE_RAG_ENTITY_GUIDANCE`）。

**调整**：改 guidance → 改 config 默认值（**改后必须重摄入**，LightRAG 实体
已固化在存储里）；改黑名单 → 改三个 `_NOISE_*` 常量（即时生效，无需重摄入）。
注意：黑名单是 demo 级语料调优，长期以 guidance 为主。

---

## D-10 Q1 枚举完整性：防截断

**背景**：LightRAG local 查询默认 `top_k=40`，枚举"组件"时 407 个实体只返回
40 个——违反 Q1"全"的指标。

**决策**：枚举查询 `top_k=400`（覆盖当前语料规模的全部实体）+ 客户端噪声过滤
（D-09）。备选方案（未采用）：直接读实体图 NetworkX 枚举——彻底但绕开
LightRAG 查询语义，等内网版上线再评估。

**实现**：`src/crucible/rag_engine.py` `enumerate_entities()` 的
`QueryParam(top_k=400)`。

**调整**：语料实体数超过 400 → 调大 top_k，或切图枚举方案。

---

## D-11 引擎降级与本地流量

**背景**：实测两个坑——(1) httpx 默认读环境代理，`http://127.0.0.1:19828`
流量被劫持到代理稳定超时（curl 直连 10ms 正常）；(2) LightRAG/嵌入模型
初始化可能失败（缺依赖、镜像不可达）。

**决策**：本地引擎流量永不走代理（`trust_env=False`）；所有引擎方法
**静默降级**为空结果/False，不抛异常——融合层按"结果形态"走保守策略，
单引擎挂掉不影响另一路。

**实现**：`src/crucible/engines/wiki_engine.py` `_CLIENT_KW`；
`src/crucible/engines/rag_engine.py` `ensure_ready()`（失败返回 False）、
各方法 try/except 返回空。

**调整**：降级行为要变成"显式告警" → 在 engines 加 logging/状态字段，
orchestrator 检查后写 notes。

---

## D-12 KB 边界：KB 枚举，Agent 选择

**背景**：KB 为漏洞验证 Agent 供知识，三步（场景构建→路径规划→POC 验证）的
**推断在 Agent**，不在 KB。WeKnora 佐证：v0.2.0 的 0-10 置信度分在 v0.8.0
被删除，充分性判断改由 prompt 指导 LLM（在消费端）。

**决策**：KB 只出知识点+溯源+差异/冲突清单，不替 Agent 做推断、不输出
"知识充分性"数字分。判别器的 confidence（D-01）只用于内部路由保守策略，
不外泄为答案置信度。

**实现**：`src/crucible/schemas.py` `FusionResponse` 契约——results 带
provenance，differences/conflicts 并列呈现由上层裁决。

**调整**：如果未来要加"充分性信号" → 放 MCP 工具层（消费端），不进融合层。

---

## D-13 运行环境约定

**背景**：网络受限（HF 阻断、GitHub 间歇阻断）、无 Anthropic 模型可用。

**决策**：
- conda 环境 `crucible`（Python 3.12）：lightrag-hku 1.5.7（tuna 镜像安装，
  aliyun 只有 rc 版）、sentence-transformers + bge-small-zh-v1.5（dim 512）、
  tiktoken cl100k_base 本地缓存
- `HF_ENDPOINT=https://hf-mirror.com`（`rag_engine.py` 模块导入前 setdefault）
- LLM：DeepSeek（`CRUCIBLE_LLM_*`，默认 `deepseek-chat`）
- 语料：中文（`CRUCIBLE_RAG_LANGUAGE` 默认 Simplified Chinese，L1 必做）

**实现**：`src/crucible/config.py` 全部 env 约定；`rag_engine.py` 的
HF_ENDPOINT 预设。

**调整**：内网 LightRAG demo 上线 → 替换 `rag_engine.py` 实现，配置项不变。

## D-14 多产品软隔离: related_projects 联邦检索

**背景**：多产品各自独立索引 (硬隔离), 但产品间存在关联场景 (如基站与网管
MAE)。完全隔离时关联知识不可见; 完全融合则污染主结果。

**决策**：声明式软隔离 —— 注册时人工声明 `related_projects`; 查询可选
`include_related` → 主项目全量检索 (全权重), 每个关联项目低量检索
(结果截断, **权重 0.1**), 进响应 `related_hits` 参考区, **永不进 M1/M2/M3
合并**。只追一级关联, 不递归。自动相似度关联不做 (引入失真层, 声明式才可控)。

**实现**：`backend/app/models.py` `Project.related_projects` (JSON);
`backend/app/routers/fusion.py` `_related_hits` (权重 0.1); 前端
QueryConsole 折叠参考区; MCP `kb_enum/kb_mechanism` 的 `related` 参数 (默认关)。

**调整**：改权重 → fusion.py `_related_hits` 的 `weight`; 改截断条数 →
`resp.results[:10]` (enum) / `[:5]` (query); 改是否递归 → 循环结构。

## D-15 多产品桥接与数据隔离约定

**背景**：llm-wiki 与 crucible 是两个系统, 靠 `wiki_project_id` 桥接。
实测两个坑: ① "current" 是 llm-wiki 的动态别名 (指向当前打开项目),
用它桥接会随打开动作漂移导致**跨项目串库**; ② llm-wiki 项目目录必须含
`schema.md` 否则 open project 拒绝。

**决策**：
- crucible 注册的 `wiki_project_id` 必须用 llm-wiki `/api/v1/projects`
  返回的**稳定 uuid**, 禁用 "current"
- 新产品建项：llm-wiki 建项目 (目录带 schema.md) → 拿 uuid → crucible 注册
  (path 指向同一宿主机目录的容器内路径, rag_workdir 独立)
- 数据共享：llm-wiki 与 crucible 挂载同一宿主机目录 (两扇窗口, 零复制);
  docreader 不挂数据, 只走 HTTP

**实现**：约定为主, 无强制校验 (后续可加: 注册时校验 uuid 格式并警告
"current")。

**调整**：校验逻辑加在 `backend/app/routers/projects.py` create_project;
schema.md 检查属 llm-wiki 侧 (py-llm-wiki 仓库)。
