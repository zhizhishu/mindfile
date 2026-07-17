# Project Tidy & Categorization (mindfile reference)

When a project folder is messy — duplicate copies, scattered predecessor/reference
material, possibly mixed-in private files or plaintext secrets — tidy it into a
**four-layer structure + one public/local boundary** so the repo is clean,
deployable, and never leaks secrets. Language/framework-agnostic. Replace
`<placeholders>` with real values.

This is a **move-files operation = high-risk (global rule §9)**: always inventory and
propose first, back up before moving/deleting, and never delete without explicit user
confirmation. The MEMORY layer here is exactly the files `mindfile` already owns — do
not duplicate them, reuse the Minimum File Set.

## Target structure (four layers)

```
<project-root>/                      ← the one authoritative, deployable project; CODE STAYS AT ROOT
│
├─ [PROJECT] deployable app code     ← the ONLY layer pushed to a public repo ✅
│   src/  app/  public/  services/  deploy/  config/
│   Dockerfile  docker-compose*  manifest(package.json/pyproject.toml/go.mod/…)  README  LICENSE/NOTICES
│
├─ [MEMORY] agent/project memory (at root)            ← local only 🔒  (= mindfile's files)
│   PROJECT_ID.md  AGENTS.md  PROJECT_CONTEXT.md  TASK.md  LOG.md  (opt PROJECT_MAP.md)
│
├─ reference/  [REFERENCE] upstream/predecessor/borrowed·forked code   ← local only 🔒
│   UPSTREAMS.md (upstream attribution)  <predecessor>/  <forked-unique-code>/  *-DISTILLED.md
│
└─ archive/   [ARCHIVE] backups/history/secrets/retired docs           ← local only 🔒
    secrets/        🔑 the ONLY place for plaintext secrets (KEYS.md + .env/runtime files)
    planning-docs/  retired internal docs no longer public
    <old-clone>-memory/          old copy's memory originals
    <old-clone>-history.bundle   old repo's full git history (`git bundle --all`)
```

**Iron rule — CODE STAYS AT ROOT.** Never nest app code into a `project/` subfolder for
tidiness: Dockerfile, build context, and deploy paths assume code at root; moving it
breaks build/deploy.

## Public / local boundary (the one dividing line)

Only the PROJECT layer is public. MEMORY + `reference/` + `archive/` are local-only.
Enforce with one clearly-marked `.gitignore` block (don't scatter the rules):

```gitignore
# --- local-only: never push (mindfile project-tidy boundary) ---
/reference/
/archive/
# project memory (agent/local)
/PROJECT_ID.md
/AGENTS.md
/PROJECT_CONTEXT.md
/TASK.md
/LOG.md
/PROJECT_MAP.md
```

(If the project legitimately publishes its own README/LICENSE at root, keep those
tracked — only the memory files + `reference/` + `archive/` are excluded.)

## Secrets rule (aligns with the global credential policy)

- `archive/secrets/` is the **single** location for plaintext secrets. Nowhere else.
- 凭证/cookie/密钥 **可本地落盘**（项目需要时）—— but **绝不外发上传**, and **绝不进 git**.
- 拿不准某个敏感值要不要留 → 问用户, **不一刀切删**.
- During tidy, **scan for stray secrets outside `archive/secrets/`** (`.env`, tokens,
  `KEYS` in code/docs) and relocate them into `archive/secrets/`.

## Tidy SOP (inventory → classify → boundary → verify → log)

1. **Confirm root + boundary first** (`scripts/mindfile_guard.py inspect "<root>"`); never
   tidy a storage-root; require `root_confidence: high`.
2. **Inventory** the root: files/dirs, duplicate copies, stray predecessor/reference
   material, suspected private files, suspected secrets.
3. **Classify** each item → PROJECT / MEMORY / `reference/` / `archive/` / delete-candidate.
   When unsure, default to keep (`reference/` or `archive/`), not delete.
4. **Propose the move plan to the user** (high-risk file moves). Back up first: copy the
   tree, and `git bundle --all` any nested old repo before touching it.
5. **Apply** after confirmation: create the four layers, move items, relocate stray
   secrets into `archive/secrets/`.
6. **Set the public/local boundary**: add/extend the `.gitignore` block above.
7. **Verify**: `git status` shows only PROJECT-layer files tracked/staged; grep confirms
   no plaintext secret outside `archive/secrets/`; run `scripts/mindfile_guard.py audit
   "<root>"` for ghost/dedup hygiene on the MEMORY layer.
8. **留痕 (record)**: append a tidy entry to `LOG.md` (what moved where, what was archived,
   boundary set) and update `TASK.md`.

## Notes

- The MEMORY layer = mindfile's Minimum File Set; reuse it, don't fork a parallel copy.
- Deleting anything is high-risk (§9): suggest, back up, confirm — never auto-delete.
- This is the standard tidy shape for any messy repo, any language/framework.
