---
name: auto-memory
description: 跨平台读写共享的全局 auto-memory 仓 (通用经验/偏好/工作纪律)。Claude 会自动加载 MEMORY.md，但 codex/cursor 不会——本 native-cli 让三家都能 recall(读)/record(写) 同一份仓，实现通用记忆全平台同步。当在 codex/cursor 里想加载或沉淀通用经验、或任意端要写一条 Claude 兼容格式的记忆时用。
---

# auto-memory (native-cli)

## 治什么

Claude Code 会把 `MEMORY.md` 自动加载进上下文；**codex/cursor 没有这个机制**，所以"通用经验/偏好/工作纪律"这层记忆一直是 Claude 独享、其它两端读不到也写不进。本 CLI 把这份仓变成三家共用：

- **单一真源**：仓 = `~/.claude/memory/`（默认；可用环境变量 `AUTOMEMORY_DIR` 覆盖仓路径；实际路径见 CLI 的 `store_dir()`）。三家(claude/codex/cursor)都读写**这同一份**。**结构=MOC 两级索引**：`MEMORY.md`(根, 自动加载, 只有①常驻必读+子索引目录) + `MEMORY.tools.md`(②工具SOP) + `MEMORY.projects.md`(③各项目) + 每条一个 `<slug>.md` fact。详见下方「记忆代谢」。
- **recall = 读**：codex/cursor 在会话开始/接手项目时跑 `recall`，把索引（+按需的命中全文）拉进上下文，等于手动版"自动加载"。
- **record = 写**：任意端都能按 **Claude 兼容格式**（frontmatter `name/description/metadata.type` + `MEMORY.md` 索引行）落一条，下次 Claude 自动加载、codex/cursor `recall` 都能见到。闭环。

> 分工别混：**项目事**（某项目的目标/约束/进度）→ 各项目 `PROJECT_CONTEXT`（走 `mindfile`）；**网站操作经验**（按 host）→ `site-memo`；**通用经验/偏好/纪律**（跨项目、跨端都该记得的）→ 本 auto-memory 仓。project 类别尽量别堆在全局仓（`automemory_audit.py` 会提示迁回）。

## CLI

```
python <各家 skills 目录>/auto-memory/automemory_cli.py <cmd>
# 三家各调自己 skills 目录下的这份 CLI；仓都指向同一份 claude 真源(见 store_dir())
```

- `recall [-q "<关键词>"] [--index tools|projects] [--max N] [--json]` — 光杆打印根 `MEMORY.md`(①常驻必读 + 子索引 footer)；`--index tools|projects` 拉对应子索引全文；`-q` 附带命中记忆的**全文**(搜全部 fact, 按相关度排, 默认前5, 子索引里的条照样命中)。**codex/cursor 加载记忆的入口**(光杆只加载小根, 工具/项目索引按需 `--index` 或 `-q` 拉)。
- `record --name <slug> --type <user|feedback|project|reference> --desc "<一行摘要>" --body "<正文>" [--title T] [--hook H] [--section ①/②/③] [--durability axiom|pattern|workaround|project-state] [--force]` — 建 `<slug>.md` + 把索引行**按区路由**插到对应索引文件末尾(①→根 MEMORY.md / ②→MEMORY.tools.md / ③→MEMORY.projects.md)，**写后立即回读校验**。`--durability` 写进 frontmatter 供 audit 冷打分(axiom 永不归档)。同名需 `--force`。
- `list [--type T] [--json]` — 列所有记忆 name/type/description。
- `get --name <slug> [--json]` — 打印单条全文。
- `doctor [--json]` — 健康检查：仓存在 / `MEMORY.md` 可解析 / 链接完整（断链·孤儿）。**自检入口。**
- `list_tools [--json]` — 契约清单。

## 典型用法

- **codex/cursor 开工加载记忆**：`automemory_cli.py recall`（或 `-q "<本轮主题>"` 顺带拉相关全文）。
- **沉淀一条通用经验**（任意端）：
  `automemory_cli.py record --name prefer-x-over-y --type feedback --desc "一句话" --body "正文。**Why:** … **How to apply:** …"`
- **更新已有条目**：加 `--force`（原地覆盖，索引不重复插）。

## 记忆代谢: MOC 两级索引 + 耐久类型 + 反思固化 (治 MEMORY.md 索引超限 + 陈旧)

MEMORY.md 每会话**整份自动加载**——一行一条堆到 100+ 就超读取上限(~24.4KB)。治本 = **MOC 两级索引**(2026-07-17 上线), 不是"按新近度分冷热":

- **① MOC 两级结构**: `MEMORY.md`(根, 自动加载, 要小) = **①常驻必读全留 + 一张"分类子索引目录"**(②→`MEMORY.tools.md` / ③→`MEMORY.projects.md`)。②③ 的索引行**全在子索引文件里、不自动加载、按需读**。这样自动加载层大小**只跟类目数挂钩、与总条数脱钩**——涨到几百条也不爆预算。`recall`(光杆)打印根+子索引 footer; `recall --index tools|projects` 拉子索引全文; `recall -q` 搜全部 fact 全文(**零信息损失**, 子索引里的条照样命中)。一次性重构器 `~/.claude/tools/automemory_restructure.py`(dry-run 默认, --apply 先备份, 绝不动 fact)。
- **② 耐久类型标签**(record `--durability axiom|pattern|workaround|project-state` → frontmatter `metadata.durability`): 让保留由**内在耐久度**驱动而非 mtime——`axiom`(铁律)永不归档、`workaround`(工具/版本相关)会过气加冷权重。治"真铁律放 40 天没碰被误判冷"。
- **④ 冷/重要性打分**(`~/.claude/tools/automemory_audit.py` 的 `cold_split`, 只读): 区 × 新近(mtime) × 反链 × durability × 取代信号 → ②③ 候选。**读取上限只针对根 MEMORY.md 自动加载层**(子索引不占预算)。①锁热 / durability=axiom 锁热 / 枢纽(有反链)排除。只建议。
- **③ 反思固化**(LLM 层, 定期): 读一批冷/重叠条 → 合并重叠(dedup)、砍已取代(§9)、项目专属条踢回各项目 `PROJECT_CONTEXT`(走 mindfile)。**移动/删除 = §9, 动前把清单给用户点头。**
- 旧的"热/冷拆分"(`automemory_compact.py`, MEMORY.md↔MEMORY.archive.md)已被 MOC 取代(archive 溶解回子索引); compact 保留作最粗兜底, 但 MOC 是主结构。
- `doctor`/audit 已 **MOC 感知**: 链接扫全部索引文件(根+tools+projects+archive)、被移进子索引的不算孤儿; `list_files` 排除全部 `MEMORY*.md` 索引本体。

## 注意（坑）

- **store 路径不被 skill_sync 重写**：源码用 `Path.home()` 分段拼路径（各目录名单独带引号），`skill_sync` 只重写**路径里带分隔符的 claude 目录串**、抓不到带引号的字符串段，所以三家 copy 的仓路径都不被改、都指向 claude 那份。**改源码时严禁把 claude 目录名与分隔符连写成路径串**（会被重写成各家自己的仓、破坏共享）。逃生口：设环境变量 `AUTOMEMORY_DIR` 覆盖仓路径。
- **写=真改全局记忆**（§9 高风险）：`record`/`--force` 落地的是共享真仓，动手前想清楚归属（通用经验才进这里，项目事/网站经验分别走 mindfile/site-memo）。
- **完整体检**走 claude tools 下的 `automemory_audit.py`（去重/迁移候选/site-memo 重叠/超长索引行）；本 CLI 的 `doctor` 只做轻量链接完整性。
- Claude 侧本来就自动加载 + 用 Write 直接写，`recall`/`record` 主要给 **codex/cursor** 补齐读写能力（三家统一入口，Claude 用也行）。
- Windows：`python`（非 `python3`）；含中文/多行正文的参数用单引号包整段，别硬拼转义。
