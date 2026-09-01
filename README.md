# Crucible

面向漏洞验证 Agent 的攻击知识库与融合检索策略设计。

> 名字含义：知识放进真实环境熔炼（实测验证），只有经得起考验的知识才获得检索权重，并在试炼中复利变准。

## 文档

| 文档 | 内容 |
|------|------|
| [docs/DESIGN-vuln-agent-kb.md](docs/DESIGN-vuln-agent-kb.md) | 漏洞验证 Agent 知识库设计：知识分类学（K1 场景 / K2 机制 / K3 经验 / K4 威胁）、知识-决策边界（KB 枚举，Agent 选择）、验证反馈闭环、实施路线 |
| [docs/DESIGN-search-strategy.md](docs/DESIGN-search-strategy.md) | **核心**：融合检索策略——查询类型分类（Q1 枚举·全 / Q2 机制·准 / Q3 经验·可信 / Q4 链式·链）、意图判别器、四模式合并（M1 并集 / M2 一致性 / M3 状态排序 / M4 路径合成）、LLM 合并 Prompt 契约、失真防护 |
| [docs/DESIGN.md](docs/DESIGN.md) | 背景文档：早期 HieraExtract × LLM Wiki 三引擎融合方案（hieraextract 已降级为过渡方案） |

## 架构图

`docs/diagrams/` 下 11 张 SVG（自适应明暗主题，可直接在 Obsidian / VS Code / GitHub 中渲染）：

- `vuln-agent-architecture.svg` — 知识库总体架构（双引擎 + MCP + 验证回写）
- `fusion-search-architecture.svg` — 融合检索分层管道
- `search-software-architecture.svg` — 软件架构（组件 / 端口 / 存储）
- `retrieval-strategy-flow.svg` — 检索策略流程（判别 → 四分支 → 四模式）
- `goal-lattice.svg` — K4 目标层级与降级链（容器逃逸示例）
- `verification-state.svg` — 知识验证状态机
- 其余为早期融合方案配图（architecture / knowledge-layers / hard-vs-soft / ingestion-pipeline / retrieval-pipeline）

## 核心设计原则

1. **知识库只供知识，不替 Agent 推断** —— 路径组织、目标选择、POC 实例化、实测执行都是 Agent 的事；KB 只做枚举与召回
2. **倾向性由查询类型决定** —— 枚举要全（召回率）、机制要准（精确率）、经验要可信、路径要链完整
3. **LLM 只比对与呈现，不重写** —— 合并层禁止改写机制事实；冲突对峙输出，不裁决
4. **实测审判闭环** —— 验证结果回写（成功加权 / 拦截成负知识 / 误报修正前提），知识随验证复利变准
