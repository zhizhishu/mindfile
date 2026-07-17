#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Inspect whether a folder is safe for project-local memory files (write gate),
and whether a project has authorized a given tool/action (tool-use gate).

Two gates, same idea (deterministic, stdlib-only, no new dependencies):

  - WRITE gate  (inspect / init-plan / verify): can we safely create/modify project
    memory files in this folder? (boundary, storage-root, root_confidence)

  - TOOL-USE gate (tool-auth): is this project authorized to use <tool> for <action>?
    Reads PROJECT_ID.md's `tool_policy` block so a standing grant is honored across
    sessions instead of re-asking every turn. High-risk actions are never persisted.

parse_project_id() understands ONE level of YAML nesting and returns dotted keys
(e.g. "serena.enabled", "tool_policy.authorized"); top-level scalars keep their bare
key, so all original callers keep working.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import date
from pathlib import Path
from typing import Any


MEMORY_FILES = [
    "PROJECT_ID.md",
    "AGENTS.md",
    "PROJECT_CONTEXT.md",
    "TASK.md",
    "LOG.md",
]

MANIFESTS = [
    "package.json",
    "pyproject.toml",
    "go.mod",
    "Cargo.toml",
    "composer.json",
    "pom.xml",
    "build.gradle",
    "settings.gradle",
    "deno.json",
    "bun.lockb",
]

CODE_DIRS = ["src", "app", "apps", "packages", "lib", "tests", "test"]

# Action -> risk class. high-risk actions are NEVER persisted; always re-confirmed.
RISK_CLASS = {"skill": "low", "subagent": "medium", "high-risk": "high"}

# Memory-hygiene thresholds for the read-only `audit` pass. TASK.md must stay small
# enough to load every turn; past these limits it should be distilled.
TASK_MAX_LINES = 200
TASK_MAX_BYTES = 8192

# LOG.md is append-only and grows unbounded; past these, suggest compress_log.py
# (archives older entries; default dry-run, never deletes). Aligned with compress_log.py.
LOG_MAX_LINES = 1200
LOG_MAX_BYTES = 60 * 1024

# Lines that look like volatile tool-availability snapshots (stale fast) -> suggest removal.
STALE_TOOL_PATTERNS = [
    re.compile(r"\b(available|unavailable|reachable|unreachable|connected|disconnected|offline|online|installed|not installed)\b", re.IGNORECASE),
    re.compile(r"\b(mcp|server|tool|skill|cli|port|pid|tab|session)\b.*\b(up|down|alive|dead|healthy|status)\b", re.IGNORECASE),
    re.compile(r"\bas of\b.*\b(today|now|this session)\b", re.IGNORECASE),
    re.compile(r"\b\d{1,2}:\d{2}(:\d{2})?\b"),  # a wall-clock time embedded as a "fact"
]

# --- (4) LOG entry importance scoring / cold-entry identification -------------
# Read-only retention scoring for LOG.md / LOG.archive.md history entries. Produces
# review + prune SUGGESTIONS only; deleting memory is high-risk (global rule 9),
# so this NEVER deletes and NEVER even auto-archives -- it only surfaces candidates.

# Entry type -> retention weight. Decisions/pitfalls are the durable spine (keep);
# milestones are mid; plain 流水 / untagged churn is low-value.
LOG_TYPE_WEIGHTS = {"决策": 3, "坑": 3, "里程碑": 2, "流水": 1, "none": 1}
LOG_LOW_TYPES = {"流水", "none"}  # only these are eligible for cold-review / prune

# Type tag inside a "## <date> [type] title" header. Chinese tags + English aliases.
LOG_HEADER_TYPE_RE = re.compile(
    r"\[\s*(决策|坑|里程碑|流水|decision|pitfall|milestone|log)\s*\]", re.IGNORECASE
)
_LOG_TYPE_ALIAS = {"decision": "决策", "pitfall": "坑", "milestone": "里程碑", "log": "流水"}

# "Old" horizon, measured relative to the NEWEST parsed entry date (self-relative:
# a dormant project is not flagged wholesale just because wall-clock time passed).
COLD_AGE_DAYS = 21

# First yyyy-mm-dd in a header wins; date ranges (2026-06-20-21 / 2026-06-20~21)
# resolve to the start date.
LOG_DATE_RE = re.compile(r"(\d{4})-(\d{2})-(\d{2})")

# Conservative supersession markers: the entry's OWN conclusion was replaced / rolled
# back / voided. NOTE: bare "回退" is excluded (it usually names a rollback *plan*),
# and "别重试/别再走" are excluded (those mark a KEEP-worthy dead-end lesson, not stale).
SUPERSESSION_PATTERNS = [
    re.compile(r"已回退"),
    re.compile(r"已?废弃"),
    re.compile(r"作废"),
    re.compile(r"取代"),
    re.compile(r"已失效"),
    re.compile(r"\bsuperseded\b", re.IGNORECASE),
    re.compile(r"\bdeprecated\b", re.IGNORECASE),
    re.compile(r"\bobsolete\b", re.IGNORECASE),
]
# A supersession hit is discounted when a negator sits right before it, so
# "没有取代词" / "不可取代" / "未废弃" are NOT read as the entry being stale.
_SUPERSESSION_NEGATORS = "没无未非不勿"

# compress_log.py injects a pointer-directory block whose header contains this text;
# it is NOT a real history entry and must be skipped when scoring.
_LOG_POINTER_MARKER = "已归档历史"

# How many entries to enumerate in the (potentially long) output lists.
_SCORE_LIST_CAP = 40

_LOG_HEADER_RE = re.compile(r"^##\s+\S.*$", re.MULTILINE)
_REF_TOKEN_RE = re.compile(r"[A-Za-z][A-Za-z0-9][A-Za-z0-9._+/\\-]{2,}")
_BACKTICK_TOKEN_RE = re.compile(r"`([^`]+)`")
_ANCHOR_RE = re.compile(r"anchor:([0-9a-f]{8})")


def norm_path(path: Path) -> str:
    return str(path)


def resolve_loose(raw: str) -> Path:
    return Path(raw).expanduser().resolve(strict=False)


def is_relative_to(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


def _strip_inline_comment(value: str) -> str:
    # strip a trailing " # comment" from scalar values, but leave inline lists alone
    if value.startswith("["):
        return value
    return re.split(r"\s+#", value, 1)[0].strip().strip("` ")


def parse_project_id(path: Path) -> dict[str, str]:
    """One-level-nesting aware parser.

    Top-level scalars keep their bare key (e.g. "project_root") so all original
    callers keep working. A top-level `key:` with an empty value opens a parent;
    indented `child: value` lines under it are stored as "key.child". Deeper
    structures (YAML list items like `- path`) are ignored, as before.
    """
    data: dict[str, str] = {}
    if not path.exists():
        return data
    current_parent: str | None = None
    for raw in path.read_text(encoding="utf-8", errors="replace").splitlines():
        stripped = raw.strip()
        if not stripped or stripped.startswith("#"):
            continue
        match = re.match(r"^(\s*)([A-Za-z0-9_.-]+)\s*:\s*(.*?)\s*$", raw)
        if not match:
            # e.g. "- some/path" list items -> not a key:value, skip (as before)
            continue
        indent = len(match.group(1))
        key = match.group(2)
        value = _strip_inline_comment(match.group(3).strip("` ").strip())
        if indent == 0:
            data[key] = value
            current_parent = key if value == "" else None
        else:
            if current_parent is not None:
                data[f"{current_parent}.{key}"] = value
            else:
                data[key] = value  # fallback: orphan indented line
    return data


def slug(value: str) -> str:
    value = value.strip().lower()
    value = re.sub(r"[^a-z0-9._-]+", "-", value)
    value = re.sub(r"-{2,}", "-", value).strip("-._")
    return value or "unknown"


def has_any(path: Path, names: list[str]) -> bool:
    return any((path / name).exists() for name in names)


def find_up(start: Path, filename: str) -> Path | None:
    path = start if start.is_dir() else start.parent
    while True:
        marker = path / filename
        if marker.exists():
            return marker
        if path.parent == path:
            return None
        path = path.parent


def find_git_root(start: Path) -> Path | None:
    marker = find_up(start, ".git")
    return marker.parent if marker else None


def find_parent_storage_root(path: Path) -> Path | None:
    current = path.parent
    while current != current.parent:
        if (current / ".codex-storage-root").exists():
            return current
        current = current.parent
    return None


def count_child_projects(path: Path) -> int:
    if not path.exists() or not path.is_dir():
        return 0
    count = 0
    try:
        children = list(path.iterdir())
    except OSError:
        return 0
    for child in children:
        if not child.is_dir():
            continue
        if (child / ".codex-storage-root").exists():
            continue
        if (child / "PROJECT_ID.md").exists() or (child / ".git").exists() or has_any(child, MANIFESTS):
            count += 1
    return count


def infer_project_type(path: Path, is_storage_root: bool) -> str:
    if is_storage_root:
        return "storage"
    if has_any(path, MANIFESTS) or has_any(path, CODE_DIRS):
        return "code"
    try:
        entries = list(path.iterdir()) if path.exists() and path.is_dir() else []
    except OSError:
        entries = []
    md_count = sum(1 for item in entries if item.is_file() and item.suffix.lower() == ".md")
    config_count = sum(1 for item in entries if item.is_file() and item.suffix.lower() in {".json", ".toml", ".yaml", ".yml", ".ini"})
    if md_count >= 2 and config_count == 0:
        return "docs"
    if config_count >= 2:
        return "config"
    return "unknown"


def inspect(mode: str, raw_target: str) -> dict[str, Any]:
    target = resolve_loose(raw_target)
    exists = target.exists()
    is_dir = target.is_dir() if exists else False
    reasons: list[str] = []
    warnings: list[str] = []
    suggested_next: list[str] = []

    target_storage_marker = target / ".codex-storage-root"
    explicit_storage_root = target_storage_marker.exists()
    parent_storage_root = find_parent_storage_root(target)
    project_id_path = find_up(target, "PROJECT_ID.md") if exists else None
    project_id_data = parse_project_id(project_id_path) if project_id_path else {}
    git_root = find_git_root(target) if exists else None

    own_manifest = has_any(target, MANIFESTS) if is_dir else False
    own_code_dirs = has_any(target, CODE_DIRS) if is_dir else False
    own_readme = (target / "README.md").exists() if is_dir else False
    own_project_id = (target / "PROJECT_ID.md").exists() if is_dir else False
    child_project_count = count_child_projects(target)
    many_child_projects = child_project_count >= 3 and not (own_manifest or own_project_id or (target / ".git").exists())

    is_storage_root = explicit_storage_root or many_child_projects
    if explicit_storage_root:
        reasons.append("Target contains .codex-storage-root.")
    if many_child_projects:
        reasons.append(f"Target contains {child_project_count} child project-like folders and no single project marker.")

    declared_root: Path | None = None
    declared_root_raw = project_id_data.get("project_root")
    if declared_root_raw and declared_root_raw.lower() not in {"none", "unknown"}:
        declared_root = resolve_loose(declared_root_raw)

    conflict = False
    if project_id_path and declared_root:
        if not is_relative_to(target, declared_root) and target != declared_root:
            conflict = True
            reasons.append("PROJECT_ID.md declares a project_root that does not contain the target path.")
        if project_id_path.parent != declared_root and not is_relative_to(project_id_path.parent, declared_root):
            conflict = True
            reasons.append("PROJECT_ID.md location conflicts with its declared project_root.")

    if declared_root and not conflict:
        resolved_project_root = declared_root
    elif own_project_id:
        resolved_project_root = target
    elif git_root and not explicit_storage_root:
        resolved_project_root = git_root
    else:
        resolved_project_root = target if is_dir else None

    score = 0
    if project_id_path:
        score += 100
    if (target / ".git").exists() if is_dir else False:
        score += 80
    elif git_root and git_root == target:
        score += 80
    if own_manifest:
        score += 50
    if own_readme and own_code_dirs:
        score += 30

    if explicit_storage_root:
        root_confidence = "high"
    elif conflict:
        root_confidence = "low"
    elif project_id_path or ((target / ".git").exists() if is_dir else False) or own_manifest:
        root_confidence = "high"
    elif git_root and has_any(git_root, MANIFESTS):
        root_confidence = "high"
    elif own_readme and own_code_dirs:
        root_confidence = "medium"
    elif many_child_projects:
        root_confidence = "high"
    else:
        root_confidence = "low"

    if not exists:
        reasons.append("Target path does not exist.")
        suggested_next.append("Create or choose a concrete child project folder, then rerun the guard.")
    if exists and not is_dir:
        reasons.append("Target path is not a directory.")
    if is_storage_root:
        suggested_next.append("Choose a child project folder; do not initialize memory in this storage root.")
    if conflict:
        suggested_next.append("Resolve the PROJECT_ID.md conflict before writing.")
    if root_confidence == "low" and not is_storage_root:
        suggested_next.append("Ask the user to confirm the real project root before writing.")

    required_files = {name: ((target / name).exists() if is_dir else False) for name in MEMORY_FILES}
    missing_memory_files = [name for name, present in required_files.items() if not present]

    project_name = project_id_data.get("project_name")
    if not project_name and is_dir:
        manifest_name = None
        package_json = target / "package.json"
        if package_json.exists():
            try:
                package_data = json.loads(package_json.read_text(encoding="utf-8"))
                if isinstance(package_data.get("name"), str):
                    manifest_name = package_data["name"]
            except Exception:
                warnings.append("Could not parse package.json name.")
        project_name = slug(manifest_name or target.name)

    write_allowed = bool(
        exists
        and is_dir
        and not is_storage_root
        and root_confidence == "high"
        and not conflict
        and resolved_project_root
        and is_relative_to(target, resolved_project_root)
    )

    if mode == "verify" and missing_memory_files:
        warnings.append("Project memory file set is incomplete.")
    if mode == "init-plan" and not write_allowed:
        warnings.append("Initialization is not allowed until the guard passes.")

    return {
        "mode": mode,
        "input_path": raw_target,
        "target_path": norm_path(target),
        "exists": exists,
        "is_directory": is_dir,
        "resolved_project_root": norm_path(resolved_project_root) if resolved_project_root else None,
        "project_name": project_name,
        "project_type": infer_project_type(target, is_storage_root) if is_dir else "unknown",
        "root_confidence": root_confidence,
        "score": score,
        "is_storage_root": is_storage_root,
        "parent_storage_root": norm_path(parent_storage_root) if parent_storage_root else None,
        "project_id_path": norm_path(project_id_path) if project_id_path else None,
        "git_root": norm_path(git_root) if git_root else None,
        "write_allowed": write_allowed,
        "can_initialize_memory": write_allowed and bool(missing_memory_files),
        "missing_memory_files": missing_memory_files,
        "required_files": required_files,
        "signals": {
            "explicit_storage_marker": explicit_storage_root,
            "child_project_count": child_project_count,
            "own_project_id": own_project_id,
            "own_manifest": own_manifest,
            "own_readme": own_readme,
            "own_code_dirs": own_code_dirs,
            "project_id_conflict": conflict,
        },
        "reasons": reasons,
        "warnings": warnings,
        "suggested_next": suggested_next,
    }


def _split_list(raw: str) -> list[str]:
    return [item.strip().lower() for item in re.split(r"[,\s]+", raw or "") if item.strip()]


def tool_auth(raw_target: str, tool: str, action: str) -> dict[str, Any]:
    """Deterministic read-back gate for per-project TOOL-USE authorization.

    Decision summary:
      - high-risk action          -> NEVER authorized, always must_ask (never persisted)
      - boundary not confirmed     -> must_ask (cannot trust a grant when root is unsure)
      - no tool_policy block       -> must_ask (nothing was ever granted)
      - tool_policy.authorized!=t  -> must_ask (grant withheld)
      - subagent + subagents=true  -> authorized
      - skill + tool in skills[]   -> authorized (explicit whitelist; '*'/'all' = wildcard)
    """
    base = inspect("inspect", raw_target)
    data: dict[str, str] = {}
    pid = base.get("project_id_path")
    if pid:
        data = parse_project_id(Path(pid))

    policy_present = ("tool_policy" in data) or any(k.startswith("tool_policy.") for k in data)
    authorized_flag = data.get("tool_policy.authorized", "").lower() == "true"
    skills = _split_list(data.get("tool_policy.skills", ""))
    subagents_flag = data.get("tool_policy.subagents", "").lower() == "true"
    granted_by = data.get("tool_policy.granted_by", "")
    granted_on = data.get("tool_policy.granted_on", "")

    risk = RISK_CLASS.get(action, "unknown")
    authorized = False
    must_ask = True
    reason = ""
    source = pid or "none"

    if action == "high-risk":
        reason = ("high-risk action (delete/move-many/remote-write/kill-process): never persisted; "
                  "always confirm per action (global rule §9).")
        source = "policy:high_risk=ask-every-time"
    elif base.get("root_confidence") != "high" or base.get("is_storage_root"):
        reason = "project boundary not confirmed (root_confidence != high or storage-root); stored grant not trusted."
    elif not policy_present:
        reason = "no tool_policy block in PROJECT_ID.md; nothing was granted -> ask once, then capture it."
    elif not authorized_flag:
        reason = "tool_policy.authorized is not true; grant withheld."
    elif action == "subagent":
        authorized = subagents_flag
        must_ask = not subagents_flag
        reason = ("subagents granted (tool_policy.subagents=true)." if subagents_flag
                  else "tool_policy.subagents is not true.")
    elif action == "skill":
        t = (tool or "").lower()
        if t and t in skills:
            authorized, must_ask = True, False
            reason = f"'{tool}' is in tool_policy.skills whitelist."
        elif skills and ("*" in skills or "all" in skills):
            authorized, must_ask = True, False
            reason = "wildcard grant in tool_policy.skills."
        elif not t:
            authorized = bool(skills)
            must_ask = not bool(skills)
            reason = "no --tool given; reporting whether any skills are whitelisted at all."
        else:
            reason = f"'{tool}' is not in tool_policy.skills whitelist {skills}."
    else:
        reason = f"unknown action '{action}'."

    return {
        "mode": "tool-auth",
        "input_path": raw_target,
        "resolved_project_root": base.get("resolved_project_root"),
        "project_id_path": pid,
        "root_confidence": base.get("root_confidence"),
        "is_storage_root": base.get("is_storage_root"),
        "tool": tool or None,
        "action": action,
        "risk_class": risk,
        "policy_present": policy_present,
        "authorized": authorized,
        "must_ask": must_ask,
        "granted_by": granted_by or None,
        "granted_on": granted_on or None,
        "source": source,
        "reason": reason,
    }


def _read_lines(path: Path) -> list[str] | None:
    """Read a memory file's lines, or None if it is absent (caller reports 'skipped')."""
    if not path.exists() or not path.is_file():
        return None
    return path.read_text(encoding="utf-8", errors="replace").splitlines()


def _extract_referenced_paths(lines: list[str]) -> list[str]:
    """Best-effort: pull path-like tokens out of memory text.

    Picks up backtick-quoted tokens (e.g. `src/app.ts`) and bare `- some/path`
    list items (as used in PROJECT_ID.md boundaries / task_file / log_file). Only
    tokens that look like paths (contain a separator or a known extension) are kept,
    so prose words are not mistaken for files.
    """
    found: list[str] = []
    backtick = re.compile(r"`([^`]+)`")
    for raw in lines:
        candidates: list[str] = list(backtick.findall(raw))
        stripped = raw.strip()
        # bare list item "- path" or "key: path" value
        list_match = re.match(r"^-\s+(.*\S)\s*$", stripped)
        if list_match:
            candidates.append(list_match.group(1))
        kv_match = re.match(r"^[A-Za-z0-9_.-]+\s*:\s*(\S.*?)\s*$", stripped)
        if kv_match:
            candidates.append(kv_match.group(1))
        for token in candidates:
            token = token.strip().strip("`").strip()
            token = _strip_inline_comment(token)
            if not token or token.lower() in {"none", "unknown", "false", "true"}:
                continue
            looks_path = (
                "/" in token
                or "\\" in token
                or re.search(r"\.[A-Za-z0-9]{1,8}$", token) is not None
            )
            if looks_path and " " not in token.strip():
                found.append(token)
    # de-dup, preserve order
    seen: set[str] = set()
    ordered: list[str] = []
    for item in found:
        if item not in seen:
            seen.add(item)
            ordered.append(item)
    return ordered


def _resolve_reference(token: str, project_root: Path) -> Path:
    """Resolve a referenced path token relative to project_root if not absolute."""
    candidate = Path(token.replace("\\", "/")).expanduser()
    if candidate.is_absolute() or re.match(r"^[A-Za-z]:[\\/]", token):
        return resolve_loose(token)
    return resolve_loose(str(project_root / candidate))


def _parse_log_entries(text: str) -> list[tuple[str, str, str | None]]:
    """Split LOG text into (header, segment, anchor) by `## ` headings.

    Drops the compress_log.py pointer-directory block (not real history). `anchor`
    is the archive id sitting on the line(s) immediately BEFORE the header (that is
    how compress_log lays archived entries out), or None for live LOG.md entries.
    """
    matches = list(_LOG_HEADER_RE.finditer(text))
    out: list[tuple[str, str, str | None]] = []
    for i, match in enumerate(matches):
        header = match.group(0).strip()
        if _LOG_POINTER_MARKER in header:
            continue
        start = match.start()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        prev_end = matches[i - 1].end() if i > 0 else 0
        preface = text[prev_end:start][-200:]  # small look-back window only
        anchor_match = _ANCHOR_RE.search(preface)
        anchor = anchor_match.group(1) if anchor_match else None
        out.append((header, text[start:end], anchor))
    return out


def _entry_date(header: str) -> date | None:
    match = LOG_DATE_RE.search(header)
    if not match:
        return None
    try:
        return date(int(match.group(1)), int(match.group(2)), int(match.group(3)))
    except ValueError:
        return None


def _entry_type(header: str) -> str:
    match = LOG_HEADER_TYPE_RE.search(header)
    if not match:
        return "none"
    tag = match.group(1).lower()
    return _LOG_TYPE_ALIAS.get(tag, tag)


def _is_specific_token(token: str) -> bool:
    """A token is a 'specific downstream reference' (a surviving artifact) rather than
    a broad topic word. Broad single words (termius, subagent) return False on purpose:
    a forward file merely naming a big topic is NOT a dependency on a given LOG entry.
    """
    token = token.strip().strip("`.,;:·()（）").strip()
    if len(token) < 5:
        return False
    if "/" in token or "\\" in token or "_" in token:
        return True
    if re.search(r"\.[A-Za-z0-9]{1,8}$", token):      # filename.ext
        return True
    if re.search(r"v?\d+\.\d+", token):               # version-ish (v0.9.0, 1.19.27)
        return True
    if token.count("-") >= 2:                          # multi-part hyphen name (agent-browser-cli)
        return True
    return False


def _entry_ref_tokens(segment: str) -> list[str]:
    """Distinctive, specific tokens an entry introduced (file/hook/version/symbol names)."""
    candidates: set[str] = set()
    for quoted in _BACKTICK_TOKEN_RE.findall(segment):
        for piece in re.split(r"[\s,;:()（）]+", quoted):
            candidates.add(piece)
    for token in _REF_TOKEN_RE.findall(segment):
        candidates.add(token)
    ordered: list[str] = []
    seen: set[str] = set()
    for token in candidates:
        token = token.strip().strip("`.,;:·()（）").strip()
        low = token.lower()
        if low in seen or not _is_specific_token(token):
            continue
        seen.add(low)
        ordered.append(token)
    return ordered


def _entry_referenced(anchor: str | None, ref_tokens: list[str], forward_text_lower: str) -> str | None:
    """Return the token/anchor proving a downstream reference, or None. Being SPECIFIC
    here (see _is_specific_token) is what stops a broad topic word from over-guarding
    every entry as 'referenced' -> nothing would ever be prunable."""
    if not forward_text_lower:
        return None
    if anchor and anchor in forward_text_lower:
        return f"anchor:{anchor}"
    for token in ref_tokens:
        if token.lower() in forward_text_lower:
            return token
    return None


def _is_superseded(segment: str) -> bool:
    """True if the entry carries a genuine supersession marker, discounting matches
    that a nearby negator flips (没有取代 / 不可取代 / 未废弃)."""
    for pattern in SUPERSESSION_PATTERNS:
        for match in pattern.finditer(segment):
            preface = segment[max(0, match.start() - 3):match.start()]
            if any(ch in _SUPERSESSION_NEGATORS for ch in preface):
                continue
            return True
    return False


def _cold_reason(entry: dict[str, Any], prune: bool) -> str:
    bits: list[str] = []
    if entry["age_days"] is None:
        bits.append("undated (treated as oldest)")
    else:
        bits.append(f"{entry['age_days']}d old (> {COLD_AGE_DAYS}d)")
    bits.append(f"low-value type ({entry['type']})")
    bits.append("not referenced by PROJECT_CONTEXT/AGENTS/TASK")
    if prune:
        bits.append("carries a supersession marker (取代/废弃/作废/已回退/已失效/deprecated)")
        tail = " -> candidate to archive-or-drop AFTER user confirmation (never auto-deleted)."
    else:
        tail = " -> review whether it still earns its per-load cost; no explicit supersession marker, so keep unless user agrees."
    return "; ".join(bits) + tail


def _score_log_entries(project_root: Path, file_lines: dict[str, list[str]]) -> dict[str, Any] | None:
    """Score LOG.md + LOG.archive.md entries for retention value and surface cold /
    prune candidates. Pure read-only; returns None when there is no LOG to score."""
    sources: dict[str, str] = {}
    for name in ("LOG.md", "LOG.archive.md"):
        path = project_root / name
        if path.exists() and path.is_file():
            sources[name] = path.read_text(encoding="utf-8", errors="replace")
    if not sources:
        return None

    forward_parts = [
        "\n".join(file_lines[name])
        for name in ("PROJECT_CONTEXT.md", "AGENTS.md", "TASK.md")
        if name in file_lines
    ]
    forward_text_lower = "\n".join(forward_parts).lower()

    parsed: list[tuple[str, str, str, str | None]] = []
    for name, text in sources.items():
        for header, segment, anchor in _parse_log_entries(text):
            parsed.append((name, header, segment, anchor))

    all_dates = [d for d in (_entry_date(h) for _, h, _, _ in parsed) if d is not None]
    reference_date = max(all_dates) if all_dates else None

    scored: list[dict[str, Any]] = []
    by_type: dict[str, int] = {}
    for name, header, segment, anchor in parsed:
        entry_date = _entry_date(header)
        etype = _entry_type(header)
        by_type[etype] = by_type.get(etype, 0) + 1
        if entry_date is None or reference_date is None:
            age = None
        else:
            age = (reference_date - entry_date).days
        if age is None:
            recency = 0
        elif age <= 7:
            recency = 3
        elif age <= COLD_AGE_DAYS:
            recency = 2
        elif age <= 60:
            recency = 1
        else:
            recency = 0
        is_old = age is None or age > COLD_AGE_DAYS
        is_low_type = etype in LOG_LOW_TYPES
        type_weight = LOG_TYPE_WEIGHTS.get(etype, 1)
        ref_tokens = _entry_ref_tokens(segment)
        ref_hit = _entry_referenced(anchor, ref_tokens, forward_text_lower)
        referenced = ref_hit is not None
        superseded = _is_superseded(segment)
        score = type_weight + recency + (3 if referenced else 0) - (1 if superseded else 0)
        if score < 0:
            score = 0
        title = re.sub(r"^#+\s*", "", header).strip()
        scored.append({
            "source_file": name,
            "title": title[:120],
            "date": entry_date.isoformat() if entry_date else None,
            "age_days": age,
            "type": etype,
            "type_weight": type_weight,
            "recency": recency,
            "referenced": referenced,
            "referenced_by": ref_hit,
            "possibly_stale": superseded,
            "retention_score": score,
            "is_old": is_old,
            "is_low_type": is_low_type,
        })

    prune_candidates: list[dict[str, Any]] = []
    cold_review: list[dict[str, Any]] = []
    for entry in scored:
        # Guard: never surface high-value types (决策/坑/里程碑) or referenced entries.
        if not (entry["is_old"] and entry["is_low_type"] and not entry["referenced"]):
            continue
        item = {
            "title": entry["title"],
            "date": entry["date"],
            "source_file": entry["source_file"],
            "score": entry["retention_score"],
            "type": entry["type"],
        }
        if entry["possibly_stale"]:
            item["suggest"] = "archive-or-drop"
            item["reason"] = _cold_reason(entry, prune=True)
            prune_candidates.append(item)
        else:
            item["suggest"] = "review"
            item["reason"] = _cold_reason(entry, prune=False)
            cold_review.append(item)

    sort_key = lambda x: (x["score"], x["date"] or "")
    prune_candidates.sort(key=sort_key)
    cold_review.sort(key=sort_key)
    scored.sort(key=lambda x: (x["retention_score"], x["date"] or ""))

    return {
        "reference_date": reference_date.isoformat() if reference_date else None,
        "cold_age_days": COLD_AGE_DAYS,
        "score_scale": ("retention_score = type_weight(决策/坑=3, 里程碑=2, 流水/none=1) "
                        "+ recency(0-3) + referenced(0/3) - superseded(0/1); higher = keep"),
        "entries_evaluated": len(scored),
        "entries_per_source": {n: sum(1 for e in scored if e["source_file"] == n) for n in sources},
        "by_type": by_type,
        "prune_candidates_total": len(prune_candidates),
        "cold_review_total": len(cold_review),
        "prune_candidates": prune_candidates[:_SCORE_LIST_CAP],
        "cold_review": cold_review[:_SCORE_LIST_CAP],
        "coldest_entries": scored[:_SCORE_LIST_CAP],
        "list_cap": _SCORE_LIST_CAP,
    }


def audit(raw_target: str) -> dict[str, Any]:
    """Read-only memory-hygiene pass. NEVER modifies or deletes anything.

    Reports non-destructive findings so the agent can SUGGEST pruning to the user
    (deleting memory is high-risk, global rule §9). Reuses the boundary resolver so
    paths are checked relative to the real project_root.
    """
    base = inspect("inspect", raw_target)
    root_str = base.get("resolved_project_root")
    project_root = resolve_loose(root_str) if root_str else resolve_loose(raw_target)

    skipped: list[str] = []
    present_files: dict[str, Path] = {}
    file_lines: dict[str, list[str]] = {}
    for name in MEMORY_FILES:
        path = project_root / name
        lines = _read_lines(path)
        if lines is None:
            skipped.append(name)
        else:
            present_files[name] = path
            file_lines[name] = lines

    suggestions: list[str] = []

    # 1) TASK.md too long --------------------------------------------------------
    task_md_too_long: dict[str, Any] = {"flagged": False}
    if "TASK.md" in present_files:
        task_path = present_files["TASK.md"]
        line_count = len(file_lines["TASK.md"])
        byte_size = task_path.stat().st_size
        over_lines = line_count > TASK_MAX_LINES
        over_bytes = byte_size > TASK_MAX_BYTES
        task_md_too_long = {
            "flagged": bool(over_lines or over_bytes),
            "lines": line_count,
            "bytes": byte_size,
            "max_lines": TASK_MAX_LINES,
            "max_bytes": TASK_MAX_BYTES,
        }
        if over_lines or over_bytes:
            suggestions.append(
                f"TASK.md is large ({line_count} lines / {byte_size} bytes; limits "
                f"{TASK_MAX_LINES} lines / {TASK_MAX_BYTES} bytes). Distill completed work "
                f"into LOG.md and stable facts into PROJECT_CONTEXT.md to keep it loadable every turn."
            )

    # 1b) LOG.md too large (append-only, unbounded growth) -----------------------
    log_md_too_long: dict[str, Any] = {"flagged": False}
    if "LOG.md" in present_files:
        log_path = present_files["LOG.md"]
        l_lines = len(file_lines["LOG.md"])
        l_bytes = log_path.stat().st_size
        l_over = l_lines > LOG_MAX_LINES or l_bytes > LOG_MAX_BYTES
        log_md_too_long = {
            "flagged": bool(l_over), "lines": l_lines, "bytes": l_bytes,
            "max_lines": LOG_MAX_LINES, "max_bytes": LOG_MAX_BYTES,
        }
        if l_over:
            suggestions.append(
                f"LOG.md is large ({l_lines} lines / {l_bytes} bytes; limits "
                f"{LOG_MAX_LINES} lines / {LOG_MAX_BYTES} bytes). Archive older entries with "
                f"`mindfile/scripts/compress_log.py <project_root>` (default dry-run, never deletes)."
            )

    # 2) Ghost references (dangling) + cross-boundary references (escape project_root) --
    # ~/.claude (tools/skills/config) is a legit cross-ref target, NOT a cross-project leak;
    # only flag refs escaping BOTH project_root and ~/.claude (i.e. into sibling projects etc).
    claude_home = resolve_loose("~/.claude")
    ghost_references: list[dict[str, str]] = []
    cross_boundary_references: list[dict[str, str]] = []
    ghost_sources = ["PROJECT_ID.md", "AGENTS.md", "TASK.md"]
    for name in ghost_sources:
        if name not in file_lines:
            continue
        for token in _extract_referenced_paths(file_lines[name]):
            resolved = _resolve_reference(token, project_root)
            if not resolved.exists():
                ghost_references.append({
                    "source_file": name,
                    "reference": token,
                    "resolved_path": norm_path(resolved),
                })
            elif (not is_relative_to(resolved, project_root) and resolved != project_root
                  and not is_relative_to(resolved, claude_home)):
                cross_boundary_references.append({
                    "source_file": name,
                    "reference": token,
                    "resolved_path": norm_path(resolved),
                })
    if ghost_references:
        suggestions.append(
            f"Found {len(ghost_references)} referenced path(s) that do not exist on disk; "
            f"fix or remove the dangling reference(s)."
        )
    if cross_boundary_references:
        suggestions.append(
            f"Found {len(cross_boundary_references)} referenced path(s) resolving OUTSIDE "
            f"project_root (possible cross-project leak); confirm each is intended "
            f"(e.g. in tool_policy.managed_external_paths) or fix it."
        )

    # 3) Duplicate facts (identical non-trivial lines/headings in 2+ files) -------
    line_locations: dict[str, set[str]] = {}
    for name, lines in file_lines.items():
        for raw in lines:
            norm = raw.strip()
            if len(norm) < 12:  # skip trivial / short lines
                continue
            if norm in {"```", "```text", "```md", "```bash"} or norm.startswith("#") and len(norm) < 16:
                continue
            line_locations.setdefault(norm, set()).add(name)
    duplicate_facts: list[dict[str, Any]] = []
    for norm, files in line_locations.items():
        if len(files) >= 2:
            duplicate_facts.append({"text": norm, "files": sorted(files)})
    if duplicate_facts:
        suggestions.append(
            f"Found {len(duplicate_facts)} fact(s)/heading(s) duplicated across memory files; "
            f"keep each in exactly one layer (the most specific) and remove the duplicates."
        )

    # 4) Stale tool-availability snapshots ---------------------------------------
    stale_tool_snapshots: list[dict[str, str]] = []
    for name, lines in file_lines.items():
        for raw in lines:
            norm = raw.strip()
            if len(norm) < 8 or norm.startswith("#"):
                continue
            for pattern in STALE_TOOL_PATTERNS:
                if pattern.search(norm):
                    stale_tool_snapshots.append({"source_file": name, "line": norm})
                    break
    if stale_tool_snapshots:
        suggestions.append(
            f"Found {len(stale_tool_snapshots)} line(s) that look like volatile tool/port/time "
            f"snapshots; these rot fast and should usually not be durable memory."
        )

    # 5) LOG entry importance scoring / cold-entry identification ----------------
    # Scores LOG.md + LOG.archive.md entries and surfaces cold / prune candidates.
    # Guards: 决策/坑/里程碑 and any entry referenced by a forward file are never
    # listed. Suggestions only -- deleting memory is high-risk (global rule 9).
    log_entry_scoring = _score_log_entries(project_root, file_lines)
    if log_entry_scoring:
        n_prune = log_entry_scoring["prune_candidates_total"]
        n_cold = log_entry_scoring["cold_review_total"]
        if n_prune:
            suggestions.append(
                f"{n_prune} LOG entr(y/ies) are prune_candidates (old + low-value + unreferenced + "
                f"supersession marker). Review to archive-or-drop; never auto-delete (global rule 9)."
            )
        if n_cold:
            suggestions.append(
                f"{n_cold} old low-value LOG entr(y/ies) are unreferenced by PROJECT_CONTEXT/AGENTS/TASK "
                f"(cold_review). Consider archiving to LOG.archive.md; decisions/pitfalls and referenced "
                f"entries are excluded."
            )

    if base.get("is_storage_root"):
        suggestions.append(
            "Target resolves to a storage root; it should not hold project memory files at all."
        )

    return {
        "mode": "audit",
        "read_only": True,
        "input_path": raw_target,
        "resolved_project_root": norm_path(project_root),
        "project_id_path": base.get("project_id_path"),
        "root_confidence": base.get("root_confidence"),
        "is_storage_root": base.get("is_storage_root"),
        "skipped_files": skipped,
        "findings": {
            "task_md_too_long": task_md_too_long,
            "log_md_too_long": log_md_too_long,
            "ghost_references": ghost_references,
            "cross_boundary_references": cross_boundary_references,
            "duplicate_facts": duplicate_facts,
            "stale_tool_snapshots": stale_tool_snapshots,
            "log_entry_scoring": log_entry_scoring,
        },
        "suggestions": suggestions,
        "note": "Read-only hygiene pass; never deletes. Deleting memory is high-risk; confirm with the user.",
    }


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(
        description="Inspect project boundaries (write gate), tool-use authorization (read-back gate), and run read-only memory-hygiene audits."
    )
    parser.add_argument(
        "mode",
        choices=["inspect", "init-plan", "verify", "tool-auth", "audit"],
        help="inspect: read-only boundary check; init-plan: pre-initialization write gate; "
             "verify: check existing memory files; tool-auth: read-back gate for per-project tool authorization; "
             "audit: read-only memory-hygiene pass (non-destructive pruning suggestions).",
    )
    parser.add_argument("target_path", help="Folder to inspect.")
    parser.add_argument("--tool", default="", help="Tool/skill name for tool-auth (e.g. fast-context).")
    parser.add_argument(
        "--action",
        default="skill",
        choices=["skill", "subagent", "high-risk"],
        help="Action class for tool-auth.",
    )
    parser.add_argument(
        "--pretty",
        action="store_true",
        help="Pretty-print JSON output.",
    )
    args = parser.parse_args(argv)

    if args.mode == "tool-auth":
        result = tool_auth(args.target_path, args.tool, args.action)
    elif args.mode == "audit":
        result = audit(args.target_path)
    else:
        result = inspect(args.mode, args.target_path)
    print(json.dumps(result, ensure_ascii=False, indent=2 if args.pretty else None))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
