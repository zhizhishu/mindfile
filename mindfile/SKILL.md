---
name: mindfile
description: Manage project-local file memory, project identity, project naming, project boundaries, and layered project memory files. Use when Codex must inspect, initialize, or update PROJECT_ID.md, AGENTS.md, PROJECT_CONTEXT.md, TASK.md, LOG.md, project_name, project_root, storage-root status, or project-memory write gates, or tidy/categorize a messy project folder into a four-layer structure (code/memory/reference/archive) with a public/local boundary. Do not use for high-level discussion about memory systems, global agent design, skill design, or slash-command design unless the user asks to inspect or change project memory files or project boundaries.
---

# Mindfile

## Overview

Use this skill to keep the current project's memory in its own project files. This skill is project-local: it does not write outside the confirmed `project_root`, does not update global config, and does not create or modify other skills. It initializes, checks, names, and maintains project memory files; the actual memory lives in the current project folder.

## Memory Constitution (L0)

These hard axioms govern every memory write. They are the constitution: when any later rule is unclear, fall back to these.

- **No Execution, No Memory** — persist only information verified by actually running or doing it. Speculation, plans, and unverified guesses are not memory.
- **Verified data is sacred** — never drop or corrupt already-verified config, paths, or values during a rewrite. When unsure, leave it as-is rather than risk losing it.
- **No volatile state as content** — never store timestamps-as-facts, PIDs, ports, session ids, or tab ids as durable memory; they rot.
- **Minimal sufficient pointer** — upper layers keep only the shortest locator needed; details live in the file the pointer points to.
- **Memory edits compound** — a wrong memory re-injects every turn, so an edit is persistent damage. Prefer surgical patches over wholesale overwrites, and if there is no new verified content, skip the write entirely.

These axioms sharpen, and do not replace, the existing Anti-Memory Rules and Promotion Rules below.

## Available Script

- `scripts/mindfile_guard.py` - deterministic project-boundary inspection. Run it before creating or modifying project memory files, and use its JSON result as the write gate.

Typical commands from the skill directory:

```bash
python scripts/mindfile_guard.py inspect "<target-path>"
python scripts/mindfile_guard.py init-plan "<target-path>"
python scripts/mindfile_guard.py verify "<target-path>"
python scripts/mindfile_guard.py tool-auth "<target-path>" --tool <skill> --action <skill|subagent|high-risk>
python scripts/mindfile_guard.py audit "<project-root>"
python scripts/compress_log.py "<project-root>"            # LOG.md 历史压缩: dry-run 预览(安全默认)
python scripts/compress_log.py "<project-root>" --apply    # 归档旧条目→LOG.archive.md(先备份, archive 只增)
```

- The `audit` command is read-only memory hygiene (see Metabolism and Pruning): it reports non-destructive pruning suggestions — including per-LOG-entry importance scoring (`prune_candidates` / `cold_review`) — and never modifies or deletes files.
- `compress_log.py` mechanically compacts an overgrown `LOG.md`: archives old entries' full text to append-only `LOG.archive.md`, leaves pointers **grouped by month (`### YYYY-MM`)** and **type-aware** (explicit `[流水]` drops its pointer; `[决策]/[坑]/[里程碑]`/untagged keep one). Prints a `[hint]` when the index is long → run the reflection consolidation (Metabolism and Pruning → LOG lifecycle). dry-run default; `--apply` backs up first; never deletes archive.

Only write project memory when the result has `write_allowed: true`, `is_storage_root: false`, and `root_confidence: "high"`. If the script says the target is a storage root, ambiguous, outside the declared project, or missing, stay read-only and ask the user for a child project path or clarification.

## Memory Principles

Use these principles for our project memory system:

- Keep the core lean; do not preload every detail.
- Load memory on demand, starting from a tiny index or boundary file.
- Distill verified, repeated successful work into current-project rules.
- Archive long history, but keep the active working context short.
- Let the current project's files own the current project's facts and workflows.
- Prefer "run, verify, then crystallize" over writing speculative memory before a task succeeds.

Map layered memory into this Codex project style:

- L0 Base Rules -> already-loaded base rules plus current project `AGENTS.md`.
- L1 Project Index -> `PROJECT_ID.md` and optional `PROJECT_MAP.md`.
- L2 Stable Facts -> `PROJECT_CONTEXT.md`.
- L3 Project Workflows -> short SOP sections in current project `AGENTS.md`.
- L4 Session Archive -> append-only `LOG.md`, with current state distilled into `TASK.md`.

## Decision Flow

1. Classify the request.
   - Pure chat or high-level learning: do not create files.
   - High-level discussion about global agent design, memory-system design, skill design, or slash-command design: do not use this skill unless project memory files or project boundaries will be inspected or changed.
   - Project work, project setup, or "enter this folder": continue.

2. Resolve `project_root`.
   - Prefer the user's explicit path.
   - Prefer `PROJECT_ID.md` when present and consistent with the path.
   - If inside a git repo, use the git root unless `PROJECT_ID.md` or the user task points to a nested package/app.
   - Treat directories marked by `.codex-storage-root` as containers, not projects.
   - Before writing, run `scripts/mindfile_guard.py init-plan "<target-path>"` from the skill directory.
   - If writing is needed and the root is unclear, ask before writing.

3. Resolve `project_name`.
   - Prefer `PROJECT_ID.md` if it already declares a name.
   - Else prefer manifest names from `package.json`, `pyproject.toml`, `go.mod`, `Cargo.toml`, or similar.
   - Else use the final project folder name.
   - Normalize machine names to short lowercase slug style when writing `project_name`.
   - Keep a human display name only when useful; do not use a parent storage-root name as the project name.
   - Ask before renaming an existing project identity or when two plausible names conflict.

4. Check existing files.
   - Read `PROJECT_ID.md` first if present.
   - Read project `AGENTS.md`, `PROJECT_CONTEXT.md`, and `TASK.md` if present.
   - Do not read all of `LOG.md` by default.
   - On entry, read the `tool_policy` block (or run `tool-auth`): if a tool/subagent is
     already granted, treat it as authorized this session and do not re-ask. See Tool-Use Gate.

5. Recall first.
   - Before substantial project work, read the project's tiny index layer FIRST: `PROJECT_ID.md`, the `Tool Routes` and `SOP Index` sections in `AGENTS.md`, and the section headers of `PROJECT_CONTEXT.md`.
   - Use that index to discover which prior lessons and SOPs already exist, then pull only the relevant detail on demand instead of full-loading every file.
   - This mirrors a tiny always-resident index plus on-demand read: keep the resident footprint small, fetch the body only when a trigger matches.

6. Initialize or refresh only missing/stale files.
   - Create minimal files when a confirmed project lacks them.
   - Preserve existing user content.
   - Append history to `LOG.md`; keep `TASK.md` short.

## Scope Boundary

`mindfile` is only for the current confirmed project.

- Do not read sibling projects for memory.
- Do not write sibling projects, parent storage roots, global config, or skill folders.
- Do not promote project workflows into external skills.
- Do not use one project's memory to initialize another project.
- If useful knowledge seems broader than the current project, mention it in the final answer as a suggestion instead of writing it.

## Trigger Calibration

Use this abstract table to decide whether `mindfile` should execute. Keep examples path-free so the skill stays global.

Should trigger:

- The user asks to initialize or refresh project memory files.
- The user asks whether the current folder is a project root or a storage root.
- The user asks to maintain `PROJECT_ID.md`, `AGENTS.md`, `PROJECT_CONTEXT.md`, `TASK.md`, or `LOG.md`.
- A project task needs memory files and they are missing or stale.
- A write to project memory needs a boundary check.
- The project name, root, allowed write scope, or forbidden paths need to be derived or corrected.
- A completed task needs current state distilled into `TASK.md`.
- A meaningful milestone, decision, validation result, or rollback should be appended to `LOG.md`.
- A verified stable project fact should be promoted to `PROJECT_CONTEXT.md`.
- A repeated project-local workflow should become a short SOP in project `AGENTS.md`.
- The project folder is messy (duplicate copies, stray reference material, mixed-in private files or plaintext secrets) and needs tidying into the four-layer structure (see Project Tidy & Categorization).

Should not trigger:

- The user is only discussing memory-system ideas.
- The user is only discussing global agent design.
- The user is only discussing skill design, slash commands, or tool-routing theory.
- The user only wants a comparison, explanation, or brainstorming with no project file changes.
- The task is pure chat, a simple answer, or a temporary read-only question.
- The same project boundary is already confirmed and the next reply is ordinary conversation.
- The task is about MCP/tool design but does not inspect or change project memory.
- The task is about frontend, documents, spreadsheets, presentations, or browser checks with no project-memory work.
- The task uses local search or command output only as ephemeral evidence.
- The user explicitly says not to inspect or modify project memory files.

## Root Scoring

Use scoring as a sanity check before writing:

- `PROJECT_ID.md`: +100
- `.git`: +80
- `package.json`, `pyproject.toml`, `go.mod`, `Cargo.toml`, or similar manifest: +50
- `README.md` plus `src/`, `tests/`, `apps/`, or `packages/`: +30
- `.codex-storage-root`: force `storage-root`
- Many unrelated child projects and no single manifest: strong `storage-root` signal

Set `root_confidence`:

- `high`: confirmed by `PROJECT_ID.md`, git root plus manifest, or explicit user path with clear project markers.
- `medium`: plausible project markers but no boundary file.
- `low`: directory shape is ambiguous, especially parent folders containing many projects.

Never write project memory files when `root_confidence` is `low`.

## Storage Roots

A storage root is a folder that holds projects but is not itself a project. It may be marked by a `.codex-storage-root` file. When a storage root is detected:

- Do not create `PROJECT_ID.md`, `TASK.md`, `LOG.md`, `PROJECT_CONTEXT.md`, or project reports there.
- Use it only to locate or create a child project after the user gives a child folder name/path.
- Do not call Serena/fast-context scoped to the storage root.
- Do not use the storage-root folder name as `project_name`.
- Do not download, clone, extract, generate, or install any artifact into the storage root; every artifact must land inside a child project. The default cwd is often the storage root, so use an absolute path into the project or confirm/switch cwd first.

## Write Gate

Before creating or changing project memory files, confirm:

- `scripts/mindfile_guard.py init-plan "<target-path>"` returns `write_allowed: true`.
- `resolved_project_root` is known.
- `is_storage_root` is `no`.
- `root_confidence` is `high`.
- The write target is inside `allowed_write`.
- The write target is outside `forbidden_paths`.
- Existing `PROJECT_ID.md`, if present, does not conflict with the target path.

If any check fails, stop and ask or stay read-only.

## Tool-Use Gate

The write gate above governs *writing files*. A separate, parallel gate governs
*using tools* (skills / subagents) so a project's standing authorization is honored
across sessions instead of being re-asked every turn.

Run before invoking a skill/CLI or spawning subagents in a confirmed project:

```bash
python scripts/mindfile_guard.py tool-auth "<project_root>" --tool <skill> --action <skill|subagent|high-risk>
```

Honor the result:

- `authorized: true, must_ask: false` -> use the tool this session without re-asking.
- `must_ask: true` -> follow the reason:
  - `policy_present: false` -> the project never granted anything; ask the user once,
    then capture the grant into `PROJECT_ID.md`'s `tool_policy` block (see Memory Intake).
  - not in whitelist / `authorized` not true -> ask whether to add the grant.
  - `risk_class: high` (delete / move-many / remote-write / kill-process) -> NEVER
    persisted; confirm every time regardless of `tool_policy` (global rule §9).

`tool_policy` answers "is this project allowed to use X"; the `Tool Routes` section in
`AGENTS.md` answers "which tool for which task". Keep the two separate.

## Four-Layer Memory

Use these project files consistently. This is the practical four-file operating layer, with `LOG.md` as archive:

1. Boundary memory: `PROJECT_ID.md`
   - Project name, root, type, parent storage root, allowed read/write, forbidden paths, task/log files, tool policy.

2. Behavior memory: `AGENTS.md`
   - Project-specific instructions, commands, conventions, local constraints.

3. Long-term fact memory: `PROJECT_CONTEXT.md`
   - Stable architecture, important decisions, known pitfalls, environment facts.

4. Working memory: `TASK.md`
   - Current goal, completed work, next step, key files, validation, risks, cleanup, last updated.

Archive memory: `LOG.md`
   - Append-only history. Search/read only when historical context is needed.

Do not move project facts into this skill. If knowledge belongs to the current project, write it to that project's files. If a rule is outside the current project, `mindfile` should not write it.

When a repeated workflow appears three or more times, consider whether it should become:

- A project-local SOP in `AGENTS.md` when it changes how agents should work inside this project.
- A stable fact or decision in `PROJECT_CONTEXT.md` when it explains the project.

**Cross-tool projects (AGENTS.md interop).** Codex/Cursor read `AGENTS.md` natively; **Claude Code reads `CLAUDE.md`, not `AGENTS.md`** (verified against official docs). To share one rule set across tools, keep the shared rules in the project `AGENTS.md` and bridge from `CLAUDE.md` with a one-line `@AGENTS.md` import — on Windows use the `@import`, not `ln -s` (symlinks need admin/Developer Mode). Claude-specific additions go *below* the import. Keep `AGENTS.md`/`PROJECT_ID.md`/`PROJECT_CONTEXT.md` plain-Markdown (no tool-specific syntax) so they stay portable.

## Project Tidy & Categorization

When a project folder is messy (duplicate copies, scattered predecessor/reference material, mixed-in private files or plaintext secrets), tidy it into a **four-layer structure + one public/local boundary**: `[PROJECT]` deployable code at root (the only public layer) · `[MEMORY]` mindfile's files at root · `reference/` upstream/predecessor/forked code · `archive/` backups/history/`secrets/`. **Code stays at root** — never nest into a `project/` subfolder (breaks Docker/build/deploy). Only PROJECT is public; MEMORY + `reference/` + `archive/` are gitignored local-only; `archive/secrets/` is the single plaintext-secret location (可本地落盘、绝不外发上传、绝不进 git、拿不准问用户). Tidying moves files = **high-risk (§9)**: inventory → classify → propose → back up → apply → set `.gitignore` boundary → verify (`git status` shows only PROJECT; no stray secret outside `archive/secrets/`) → 留痕. Full spec, `.gitignore` block, and step-by-step SOP: read `references/project-tidy.md`.

## Tool Routes

When initializing or refreshing project files, add a short `Tool Routes` section to project `AGENTS.md` when useful. Keep routes near the top and project-specific. Tool routes are triggers, not essays.

Default routes:

- Project memory, project naming, `TASK.md`, `LOG.md`, `PROJECT_CONTEXT.md` -> `mindfile`.
- Tool exposure, tool failure, or a specific skill/CLI/MCP (browser automation, code search, docs lookup, design generation, etc.) -> your project's tool-router skill.
- Unknown entry point or broad codebase context -> fast-context `fast_context_search` when project boundary is confirmed; otherwise local `rg`.
- Symbol references, call chains, semantic edits, refactor impact -> Serena Pool when project boundary is confirmed; otherwise local `rg` / AST.
- UI, visual design, prototype, design review -> relevant design skill.
- Localhost/browser verification, screenshots, real browser interaction -> Browser plugin or browser skill guide.
- Documents, spreadsheets, presentations -> corresponding document/spreadsheet/presentation skill.

If a task hits a route and the agent does not call it, it must state why: boundary unclear, tool unavailable, local search enough, risk too high, or user only wants discussion.

## Memory Intake

Before writing memory, classify the information:

- Ephemeral: command output, temporary errors, one-off observations, failed guesses.
- Working state: current task goal, next step, files touched, validation, open risks.
- Stable project fact: architecture, commands, environment assumptions, project-specific constraints.
- Project workflow: repeated steps that reliably solve a class of task inside this project.
- Boundary fact: project root, storage root, allowed writes, forbidden paths, tool policy.

Write each class to the right place:

- Ephemeral -> usually do not persist; mention in the final answer if useful.
- Working state -> `TASK.md`.
- Stable project fact -> `PROJECT_CONTEXT.md`.
- Project workflow -> current project `AGENTS.md` short SOP.
- Boundary fact -> `PROJECT_ID.md` (tool/subagent authorization goes in its `tool_policy` block).
- Long historical record -> append `LOG.md` after a meaningful milestone.

## Promotion Rules

Use these thresholds so memory does not become a junk drawer:

- Write to `TASK.md` when it helps the next turn resume the current work.
- Append to `LOG.md` only after a meaningful milestone, decision, validation result, or rollback.
- Promote to `PROJECT_CONTEXT.md` only when the fact is stable and likely useful across future tasks.
- Promote to project `AGENTS.md` only when the workflow has repeated or clearly changes how agents should behave in this project.
- Keep failed attempts out of stable memory unless the failure reveals a durable pitfall.

## Anti-Memory Rules

Do not persist:

- Secrets, tokens, cookies, private browsing data, or unauthorized business data.
- Raw long command output when a short summary is enough.
- Tool availability snapshots that will go stale quickly.
- Speculative architecture guesses not verified against files or tests.
- User emotions or preferences unless the user explicitly asks to remember them.
- Temporary paths, ports, process IDs, and browser tabs after cleanup is done.

## Crystallization Loop

For substantial work, use this loop:

1. Explore with the smallest safe context.
2. Execute and verify.
3. Summarize only the useful result.
4. Decide whether it is ephemeral, working state, stable fact, project workflow, boundary fact, or history.
5. Write the smallest durable memory to the right file.
6. Keep `TASK.md` short by moving completed history to `LOG.md` and stable lessons to `PROJECT_CONTEXT.md` or project `AGENTS.md`.

## Metabolism and Pruning

Memory needs periodic hygiene so the files stay lean and trustworthy. Pruning is a review pass, not an automatic delete.

- **ROI test**: keep an entry only if (the error-probability it prevents x the cost of omission) outweighs its per-load token cost. Below that line, compress the entry or delete it.
- **Ghost-entry audit**: every file, path, or SOP referenced by an index — `AGENTS.md` `SOP Index` / `Tool Routes`, `PROJECT_ID.md` task/log paths, `TASK.md` `Key Files` — must actually exist on disk. Dangling references get fixed or removed.
- **Cold-entry review**: entries unused across many sessions are candidates for archival to `LOG.md` or deletion.
- **Dedup**: each fact lives in exactly one layer. Remove duplicates and keep the most specific location.
- Pruning **suggests, never auto-deletes**. Deleting memory is high-risk (global rule 9) and must be surfaced for explicit user confirmation.

Use `scripts/mindfile_guard.py audit "<project_root>"` for a read-only pass that reports these findings as non-destructive suggestions; it never modifies or deletes anything.

### LOG lifecycle — type-tag → compress → reflect (治 LOG 陈旧 + 太长)

`LOG.md` is append-only episodic history: it grows unbounded, and **most entries never distill into a stable fact** — so distillation alone (LOG → `PROJECT_CONTEXT.md`) leaves an un-distillable residue that just piles up. Manage it in stages, not by distillation alone:

**① Type-tag on append.** Prefix each `## <date>` title with a type: `[决策]` (why a choice was made), `[坑]` (a durable pitfall / dead-end lesson), `[里程碑]` (a shipped/closed milestone), `[流水]` (routine activity). Untagged is treated as `[流水]` but kept conservatively. Tags drive retention.

**② Mechanical compress** (`compress_log.py --apply`, deterministic): over threshold, moves old entries' full text to append-only `LOG.archive.md`, leaves pointers **grouped by month** and **type-aware** — explicit `[流水]` drops its index pointer (text still archived), decision/pitfall/milestone/untagged keep one. Prints `[hint]` when the pointer index is long → run stage ③.

**③ Reflection consolidation** — the LLM layer, what pure distillation can't do. On hint, take the oldest un-consolidated month; read its entries (pointers + `LOG.archive.md`); then (a) write a **3–5 sentence era-narrative** — what happened + the decisions/pitfalls/outcomes that explain "how we got here", not every detail; (b) **replace that month's pointer group with the narrative** in `LOG.md` (compress then treats the month as covered, won't re-expand it); (c) **promote any newly-stable fact to `PROJECT_CONTEXT.md`**; (d) raw entries stay in `LOG.archive.md`, recoverable. This bounds growth **geometrically** (a batch of episodes → one paragraph) instead of linearly (one entry → one pointer). Reflection is the answer to "distillation isn't enough": you don't force every episode into a fact — you compress a batch of episodes into a narrative.

**④ Importance audit** (`audit`, read-only) scores each LOG/archive entry by `type × recency × referenced − superseded` and surfaces `prune_candidates` (old + low-type + unreferenced + superseded → suggest archive/drop) and `cold_review` (old + low-type + unreferenced → suggest review), while guarding decisions/pitfalls/referenced entries out. **Suggests only — deleting is §9 and needs explicit user confirmation.**

### Decisions & time-sensitive facts (治 PROJECT_CONTEXT 陈旧)

The LOG metabolism above handles episodic history; two lighter conventions keep the **fact/decision layer** honest:

- **Architecture decisions → append-only `decisions.md` (optional 6th file).** When architecture-decision records start crowding `PROJECT_CONTEXT.md`, spin them into `decisions.md`: one block per decision (date · what · why · alternatives), **append-only**; when a later decision overrides an earlier one, tag the old block `SUPERSEDED by <date>` instead of deleting it. Keeps the "why we chose X" trail without letting it drown the stable-fact file.
- **Time-sensitive facts carry a validity date.** A `PROJECT_CONTEXT.md` fact that will rot (a count, a health status, a "current" state, a fast-moving external constraint) gets a `valid-as-of: YYYY-MM` marker. On later review, a fact past a reasonable staleness window is **re-verified, not trusted blindly** — mark it `needs-review`, never auto-delete. This is the fact-layer twin of "no volatile state as content": don't silently carry a stale fact forward.
- **Boundary:** a **platform/tool-general** pitfall (holds across projects) belongs in global `auto-memory`; a **this-project** decision/fact belongs in `PROJECT_CONTEXT.md`/`decisions.md`. Don't mix project specifics into the global store.

## Initialization Rules

- Never initialize a parent storage root.
- Treat `.codex-storage-root` as a hard stop for project initialization in that directory.
- Never let the storage-root name become the project name.
- Do not create project files for pure chat, simple Q&A, temporary read-only inspection, or brainstorming.
- Do not overwrite existing project memory. Add missing sections or ask if a rewrite would be safer.
- Keep generated files concise and UTF-8 without BOM.
- If a file shows encoding corruption risk, report it and avoid broad rewrites unless the user explicitly asks.

## Minimum File Set

When initializing a confirmed project, create:

- `PROJECT_ID.md`
- `AGENTS.md`
- `PROJECT_CONTEXT.md`
- `TASK.md`
- `LOG.md`

Optional:

- `PROJECT_MAP.md` for large projects or projects with repeated navigation cost.
- `decisions.md` (append-only ADR log) when architecture decisions accumulate — see the "Decisions & time-sensitive facts" note under LOG lifecycle.

For exact templates, read `references/templates.md`.

## Maintenance Rules

- Update `TASK.md` at task boundaries or before ending a substantial project turn.
- Append `LOG.md` only after meaningful milestones, not every tiny command.
- Summarize long history into `PROJECT_CONTEXT.md` only when it becomes stable project knowledge.
- Keep global rules out of project files unless the project truly needs a stricter local override.
- When project memory files are created or changed, include a `Memory Update Brief` in the final answer so the user can see what was remembered and why.

Use this format:

```text
Memory Update Brief:
- Written: <PROJECT_ID.md|AGENTS.md|PROJECT_CONTEXT.md|TASK.md|LOG.md|none>
- Reason: <why this information deserved memory>
- Location: <which layer/file received it>
- Not written: <what stayed ephemeral and why>
```

## Validation

After initialization or refresh:

- Confirm the declared `project_root` matches the target directory.
- Confirm no files were created in a storage root by mistake.
- Confirm `TASK.md` is short enough to load every turn.
- Mention any skipped files and why.
