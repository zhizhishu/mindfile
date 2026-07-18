# mindfile — 给 AI 编码 agent 的分层记忆系统

> A layered, self-metabolizing memory system for AI coding agents (Claude Code / Codex / Cursor …).
> 纯 Python stdlib、零依赖、明文可 grep、不接向量库。

AI agent 的记忆越用越多，最后**索引本身撑爆"每会话自动加载"的上下文预算**——而单纯把旧记忆"蒸馏成稳定事实"并不够，因为大量情节史根本蒸不出事实、只能干堆。`mindfile` 用一套**会自我代谢、且永久有界**的分层结构解决它。

两层，各管一摊：

| 层 | 治什么 | 核心机制 |
|---|---|---|
| **`mindfile/`** — 项目记忆 | 单个项目的边界/事实/进度/历史 | 四层文件(L0-L4) + **LOG 三阶段生命周期** |
| **`auto-memory/`** — 全局记忆 | 跨项目通用经验/偏好/工作纪律 | **MOC 两级索引** + 耐久标签 + 冷打分 |

---

## 核心思想

### 1. 渐进披露 (progressive disclosure)：小索引常驻，正文按需拉
每会话只加载一层**极小的索引**（标题 + 一句话），需要哪条正文才去读那个文件——一个 agent"知道几十条记忆"花的上下文比"激活一条"还少。这也是 Anthropic Agent Skills 的官方设计。

### 2. 别蒸馏，去反思 (reflection)：一批情节 → 一段叙事
项目 `LOG.md` 是 append-only 情节史，多数条目蒸不成稳定事实。与其硬蒸，不如**把一批老情节压成一段"时代叙事"**（保住"怎么走到今天/试过啥失败"的脉络，而非每条细节）——把线性增长压成几何增长。这是 Stanford Generative Agents 的 reflection 思路。

### 3. MOC 两级索引：常驻层大小与总条数脱钩
全局记忆不用"一个平铺大索引 + 按新近度分冷热"，而是**一张极小的"类目地图"常驻 + 每个类目的子索引单独成文件、按需读**。于是"永远加载层"的大小**只跟类目数(≈10 行)挂钩、跟总条数彻底脱钩**——涨到几百条也不爆预算。这是 Obsidian MOC (Maps of Content) 的做法。

### 4. 耐久驱动保留，不靠时间猜
每条记忆带一个**内在耐久类型**：`axiom`(铁律，永不归档) / `pattern`(可复用技法) / `workaround`(绑工具版本、会过气) / `project-state`(会腐烂)。冷打分按耐久度而非 mtime——治"真铁律放 40 天没碰被误判成冷"。

> 实现现状：冷打分里 `axiom` 锁死永不归档、`workaround` 加冷权重更易归档；`pattern` / `project-state` 目前是语义标注、评分回落到默认(mtime + 反链)，后续再细化。四类里 2 类已真正驱动行为、2 类待细化。

### 5. 文件系统 grep > 向量库
对一个百来条的精选库，`grep` 全文检索比嵌入/向量库更省更准（Letta 实测 filesystem 74% 打赢向量 68.5%），且明文可审、可版本控制。**别为百来条上向量。**

---

## `mindfile/` — 项目记忆 (project memory)

每个项目一套分层文件，边界清晰：

- **`PROJECT_ID.md`** (L1 边界)：项目名/根/类型/允许读写/禁止路径/工具授权。
- **`AGENTS.md`** (L0+L3 行为/SOP)：项目专属指令、工具路由、短 SOP。
- **`PROJECT_CONTEXT.md`** (L2 稳定事实)：架构/重要决策/已知坑/环境事实。
- **`TASK.md`** (工作记忆)：当前目标/进度/下一步/关键文件——短、每轮加载。
- **`LOG.md`** (L4 归档)：append-only 历史。
- **`decisions.md`** (可选)：架构决策 ADR 账本，append-only、过时标 `SUPERSEDED`——让"当初为什么这么选"不被 LOG 流水账淹没。

**LOG 三阶段代谢**（治"陈旧 + 太长 + 蒸馏不够"）：
1. **打标签**：`## <日期> [决策]/[坑]/[里程碑]/[流水]`，类型驱动保留。
2. **机械压** (`scripts/compress_log.py`)：超阈值把旧条搬进 `LOG.archive.md`，留**按月分组**的指针；显式 `[流水]` 连指针都不留。
3. **反思固化**：把一个纪元压成一段叙事、稳定事实提升到 `PROJECT_CONTEXT.md`。

`scripts/mindfile_guard.py` = 只读边界闸 + 记忆卫生审计（重要性打分、幽灵引用、冷条建议——**只报不删**）。

**决策与时效事实**（治 `PROJECT_CONTEXT` 陈旧）：架构决策进 append-only `decisions.md`；会腐烂的事实（在线数、"当前"状态、快变约束）标 `valid-as-of: YYYY-MM`，复核时超期**先重验、不盲信**（标 `needs-review`、绝不自动删）。这是约定纪律，非自动化功能。

**跨工具互通**：项目行为规则写在 `AGENTS.md`（Codex/Cursor 原生读）；Claude Code 只读 `CLAUDE.md`，故一行 `@AGENTS.md` 桥接即三端共用一份规则——静态文件保持 plain-Markdown、不锁死单一工具。

## `auto-memory/` — 全局记忆 (global memory)

跨项目通用经验的单一共享仓，三端(claude/codex/cursor)通读通写：

- **`automemory_cli.py`**：`recall`(读) / `record`(写，带 `--durability`) / `list` / `get` / `doctor`。
- **`automemory_restructure.py`**：把平铺索引一次性升级成 **MOC 两级结构**（根 `MEMORY.md` + `MEMORY.tools.md` / `MEMORY.projects.md`）。
- **`automemory_audit.py`**：只读冷/重要性打分（区 × 新近 × 反链 × 耐久 × 取代信号）。
- **`automemory_compact.py`**：热/冷拆分兜底器。

全程 **dry-run 默认、--apply 先备份、绝不删 fact 文件、零信息损失**（`recall -q` 搜全部 fact 全文，归档的照样命中）。

---

## 快速上手

```bash
# 项目记忆: 进项目做记忆前过边界闸
python mindfile/scripts/mindfile_guard.py init-plan "<项目根>"
python mindfile/scripts/mindfile_guard.py audit "<项目根>"      # 只读卫生审计
python mindfile/scripts/compress_log.py "<项目根>" --apply      # LOG 压缩(先 dry-run)

# 全局记忆(仓默认 ~/.claude/memory, 可用 AUTOMEMORY_DIR 覆盖)
python auto-memory/automemory_cli.py recall -q "<主题>"         # 读
python auto-memory/automemory_cli.py record --name x --type feedback --desc "..." --body "..." --durability axiom
python auto-memory/automemory_audit.py                          # 冷打分建议
```

纯 Python 3 标准库，无需 `pip install`。Windows 用 `python`（非 `python3`）。各 skill 的 `SKILL.md` 是给 agent 读的完整说明书。

## 设计出处 (further reading)

progressive disclosure ([Anthropic](https://www.anthropic.com/engineering/effective-context-engineering-for-ai-agents)) · tiered memory / OS-paging ([MemGPT](https://arxiv.org/abs/2310.08560), [Letta](https://www.letta.com/)) · reflection (Stanford Generative Agents) · MOC / Maps of Content ([LYT](https://notes.linkingyourthinking.com/Cards/MOCs+Overview)) · filesystem-over-vector ([Letta benchmark](https://www.letta.com/blog/benchmarking-ai-agent-memory)) · temporal invalidation ([Zep/Graphiti](https://arxiv.org/abs/2501.13956)) · 见 [`docs/DESIGN.md`](docs/DESIGN.md)。

## License

[MIT](LICENSE)
