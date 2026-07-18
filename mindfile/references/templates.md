# Mindfile Templates

Use these templates as starting points. Keep them concise and adapt to the project.

## Naming Rules

- `project_name` is the stable machine-friendly name.
- Prefer an existing `PROJECT_ID.md` name, then package/manifest name, then final folder name.
- Use short lowercase slug style for new `project_name` values, for example `cliproxy` or `remnawave-panel-fork`.
- Do not use a parent storage-root name such as `claude`.
- If the folder name and manifest name conflict, keep the existing identity or ask the user.

## Storage Root Marker

Place this file in a parent folder that only stores projects:

```md
# .codex-storage-root

This directory is a storage root, not a project root.

Rules:
- Do not create PROJECT_ID.md, TASK.md, LOG.md, PROJECT_CONTEXT.md, or reports here.
- Do not download, clone, extract, generate, or install any artifact here; every artifact must land inside a child project.
- Use this directory only to locate child projects.
- Ask for a child folder before writing project files.
```

## PROJECT_ID.md

```md
# PROJECT_ID.md

project_name: <name>
display_name: <optional human name>
project_root: <absolute path>
project_type: <code|docs|config|storage|unknown>
parent_storage_root: <absolute path or none>
purpose: <one sentence>
task_file: <absolute path>\TASK.md
log_file: <absolute path>\LOG.md
root_confidence: high
is_storage_root: false

serena:
  enabled: false
  required: false
  reason: <when useful>

boundaries:
  allowed_read:
    - <absolute project_root>
  allowed_write:
    - <absolute project_root>
  forbidden_paths:
    - <paths that must not be touched>

startup_check:
  - Confirm this file before writing project files.
  - Read project AGENTS.md, PROJECT_CONTEXT.md, and TASK.md when present.
  - Do not read LOG.md unless history is needed.
```

## AGENTS.md

```md
# AGENTS.md

## Project Identity

- Project root: `<absolute path>`
- Project type: `<code|docs|config|storage|unknown>`
- Parent storage root: `<absolute path or none>`

## Local Rules

- Follow already-loaded base rules first.
- Keep reads and writes inside `project_root` unless explicitly authorized.
- Do not write task files in the parent storage root.

## Tool Routes

- Project memory, project naming, `TASK.md`, `LOG.md`, `PROJECT_CONTEXT.md` -> `mindfile`.
- Tool exposure, tool failure, agent-browser-cli, Chrome, Serena, fast-context, Stitch, Gitee, Context7, DDG, Desktop Commander, Sequential Thinking -> `base`.
- Unknown entry point or broad codebase context -> fast-context `fast_context_search` when project boundary is confirmed; otherwise local `rg`.
- Symbol references, call chains, semantic edits, refactor impact -> Serena Pool when project boundary is confirmed; otherwise local `rg` / AST.
- UI, visual design, prototype, design review -> relevant design skill.
- Localhost/browser verification, screenshots, real browser interaction -> `agent-browser-cli`(真实登录态)；操作某站点前 `recall`、操作后 `record` 走 `site-memo`(网站经验跨 Claude/Codex/Cursor 共享，与项目级 mindfile 各管一摊)。
- Documents, spreadsheets, presentations -> corresponding document/spreadsheet/presentation skill.

If a route matches and the tool is not called, state why: boundary unclear, tool unavailable, local search enough, risk too high, or user only wants discussion.

## SOP Index

Lists this project's reusable SOPs by trigger keyword so agents can recall index-first and pull only the matching SOP body on demand. Keep it to one short line per entry; this is a keyword map, not prose.

```text
sop_name(trigger keyword) | other_sop(keyword) | deploy_sop(release, publish)
```

## Commands

- Install: `<command or unknown>`
- Test: `<command or unknown>`
- Run: `<command or unknown>`

## Task Handoff

- Current task: `TASK.md`
- History archive: `LOG.md`
- Stable project facts: `PROJECT_CONTEXT.md`
- Long `LOG.md` is not loaded by default.

## Project SOP

- Confirm `PROJECT_ID.md` before writing project memory.
- Run the Mindfile guard before initializing or changing project memory files.
- Keep `TASK.md` short; move completed history to `LOG.md` when it becomes useful history.
- Promote only verified stable facts to `PROJECT_CONTEXT.md`.
- Promote repeated project workflows to short SOPs in this `AGENTS.md`.
- Do not invoke project-memory workflows for pure discussion, ordinary chat, or tasks with no project-memory read/write need.

## Memory Intake

- Ephemeral command output and failed guesses are not persisted by default.
- Current goal, next step, touched files, validation, and open risks go to `TASK.md`.
- Stable architecture, commands, constraints, and durable pitfalls go to `PROJECT_CONTEXT.md`.
- Repeated project-specific workflows go to this `AGENTS.md` as short SOPs.
- Workflows stay in this project. Do not create or update external skills from this template.
- Milestones, decisions, validation results, and rollbacks are appended to `LOG.md`.

## Memory Update Brief

When `PROJECT_ID.md`, `AGENTS.md`, `PROJECT_CONTEXT.md`, `TASK.md`, or `LOG.md` is created or changed, the final answer must say:

```text
Memory Update Brief:
- Written: <file list / none>
- Reason: <why this information deserved memory>
- Location: <memory layer/file>
- Not written: <what stayed ephemeral and why>
```

## Promotion Rules

- Promote only verified facts, not guesses.
- Keep `TASK.md` short enough to load every turn.
- Move completed history to `LOG.md`.
- Move stable lessons to `PROJECT_CONTEXT.md` or short SOPs.
- Do not persist secrets, raw long outputs, temporary ports/process IDs, or stale tool snapshots.
```

## PROJECT_CONTEXT.md

```md
# PROJECT_CONTEXT.md

## Purpose

<What this project is for.>

## Stable Facts

- <Architecture, modules, conventions, or environment facts that remain true.>

## Decisions

- <Date>: <decision and reason>

## Known Pitfalls

- <Pitfall and how to avoid it>

## Reusable Patterns

- <Stable workflow or SOP worth remembering for this project>

## Tool Preferences

- <Project-specific tool route or validation preference>
```

## TASK.md

```md
# TASK.md

## Current Goal

<One short current objective.>

## Completed

- <What has been done.>

## Next Step

- <The next concrete action.>

## Key Files

- `<path>` - <why it matters>

## Validation

- <Tests/checks run, or "not run yet">

## Risks / Pending

- <Open risk or question>

## Resource Cleanup

- <Processes, ports, tabs, or temporary files started this turn; otherwise none>

## Last Updated

<YYYY-MM-DD>
```

## LOG.md

```md
# LOG.md

## <YYYY-MM-DD> - <short milestone>

- Goal: <what changed>
- Files: <important files touched>
- Validation: <checks run>
- Notes: <stable finding or follow-up>
```
