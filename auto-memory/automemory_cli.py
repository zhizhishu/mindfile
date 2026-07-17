#!/usr/bin/env python3
# -*- coding: utf-8 -*-
r"""automemory_cli.py — 跨平台读写共享全局 auto-memory 仓 (native-cli, stdlib only).

背景: Claude Code 会自动加载 MEMORY.md 进上下文; codex/cursor 没有这个机制。
本 CLI 让三家 (claude/codex/cursor) 都能 recall(读) / record(写) **同一份** auto-memory 仓,
让"通用经验/偏好/工作纪律"真正全平台同步。仓 = 单一真源, 在共享的 claude home 下。

契约 (base native-cli): --json 走 JSON stdout; 命令 recall/record/list/get/doctor/list_tools。
零第三方依赖。绝不灌坏文件: 写后立即回读校验 (对齐 auto-memory 的"防噪音灌坏文件"铁律)。

⚠️ 路径构造硬约束: store 常量必须用分段 `Path.home() / ".claude" / ...` 拼,
   **绝不能在源码里写 `.claude/` 或 `.claude\` 连续串** —— skill_sync 会把带分隔符的
   `.claude/` 重写成 `.codex/`/`.cursor/`, 那样三家就写去各自的仓、不再是同一份了。
   分段的 `".claude"`(带引号)不被重写, 故三家 copy 都指向 claude 那一份。
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path

try:  # Windows 控制台默认 cp1252, 强制 utf-8 免中文炸
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
except Exception:
    pass

VERSION = "0.2.0"
VALID_TYPES = ("user", "feedback", "project", "reference")
# 类型 → 默认落在哪一区 (区标记见各索引文件的 `## <marker>` 标题行)
TYPE_SECTION = {"user": "①", "feedback": "①", "reference": "②", "project": "③"}
# MOC 两级索引: 区 → 该区索引行落在哪个文件。
#   ① 常驻必读 → 根 MEMORY.md(自动加载); ② 工具 → MEMORY.tools.md; ③ 项目 → MEMORY.projects.md。
ROOT_INDEX = "MEMORY.md"
SECTION_INDEX_FILE = {"①": "MEMORY.md", "②": "MEMORY.tools.md", "③": "MEMORY.projects.md"}
SUB_INDEX_FILES = ("MEMORY.tools.md", "MEMORY.projects.md")
# 所有索引文件(非 fact): list/recall -q/doctor 都不把它们当 fact 文件。
INDEX_FILES = {"MEMORY.md", "MEMORY.tools.md", "MEMORY.projects.md", "MEMORY.archive.md"}
# --index 别名 → 子索引文件名
INDEX_ALIAS = {"tools": "MEMORY.tools.md", "projects": "MEMORY.projects.md"}
# 区 → 子索引文件缺失时新建用的 `## 标题`
SECTION_HEADING = {"②": "## ② 工具用法 / 通用 SOP", "③": "## ③ 各项目"}
VALID_DURABILITY = ("axiom", "pattern", "workaround", "project-state")
MAX_INDEX_LINE = 200  # 与 automemory_audit.MAX_LINE 对齐: 索引行字符上限
SLUG_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")


def store_dir() -> Path:
    """共享 auto-memory 仓目录。分段拼 `.claude` 防 skill_sync 重写; 支持 env / --dir 覆盖。"""
    env = os.environ.get("AUTOMEMORY_DIR")
    if env:
        return Path(env).expanduser()
    # 注意: 分段拼, 源码里不出现 ".claude/" 连续串 (见文件头硬约束)
    return Path.home() / ".claude" / "memory"


def index_path(d: Path) -> Path:
    return d / "MEMORY.md"


# fact-bullet 索引行: `- [标题](slug.md) ...` (排除头部 blockquote 里的 `(slug.md)` 示例 / 📂 目录 bullet)
_FACT_BULLET_RE = re.compile(r"^\s*-\s*\[.+?\]\(")


def index_link_files(text: str) -> set[str]:
    """只从 fact-bullet 行抽 md-link 目标文件名(如 `foo.md`)。头部示例/目录 bullet 不算链接。"""
    out: set[str] = set()
    for ln in text.splitlines():
        if _FACT_BULLET_RE.match(ln):
            out.update(re.findall(r"\]\(([^)]+\.md)\)", ln))
    return out


# ---------- 解析 / 工具 ----------

def parse_frontmatter(text: str) -> dict:
    """极简 frontmatter 解析: 取 name/description/type/durability(在 metadata 下)。不依赖 yaml 库。"""
    fm = {"name": "", "description": "", "type": "", "durability": ""}
    m = re.match(r"(?s)^---\s*\n(.*?)\n---", text)
    block = m.group(1) if m else text[:600]
    mm = re.search(r"(?m)^\s*name:\s*(.+?)\s*$", block)
    if mm:
        fm["name"] = mm.group(1).strip().strip('"').strip("'")
    md = re.search(r"(?m)^\s*description:\s*(.+?)\s*$", block)
    if md:
        fm["description"] = md.group(1).strip().strip('"').strip("'")
    mt = re.search(r"(?m)^\s*type:\s*([A-Za-z]+)", block)
    if mt:
        fm["type"] = mt.group(1).strip().lower()
    mdur = re.search(r"(?m)^\s*durability:\s*([A-Za-z-]+)", block)
    if mdur:
        fm["durability"] = mdur.group(1).strip().lower()
    return fm


def list_files(d: Path) -> list[Path]:
    # 排除所有索引文件(MEMORY.md / MEMORY.tools.md / MEMORY.projects.md / MEMORY.archive.md):
    # 它们不是 fact, 否则 recall -q 会命中索引本体当噪音、list 计数偏高、doctor 误报 orphan。
    # 用精确集合(非前缀)以免误伤真 fact(理论上叫 memory-*.md 的不会命中, 但集合最稳)。
    return sorted(f for f in d.glob("*.md") if f.name not in INDEX_FILES)


_STOP = {"the", "and", "for", "with", "why", "how", "apply", "type", "name", "description"}


def tokenize(text: str) -> set[str]:
    """英文词(>=3) + 中文 2-gram, 用于 recall 的相关度打分 (对齐 audit 的分词思路)。"""
    low = (text or "").lower()
    low = re.sub(r"\[\[[^\]]+\]\]", " ", low)
    low = re.sub(r"`[^`]*`", " ", low)
    toks: set[str] = set()
    for w in re.findall(r"[a-z0-9_]{3,}", low):
        if w not in _STOP:
            toks.add(w)
    for run in re.findall(r"[一-鿿]+", low):
        if len(run) == 1:
            toks.add(run)
        else:
            toks.update(run[i:i + 2] for i in range(len(run) - 1))
    return toks


def _err(msg: str, as_json: bool) -> int:
    if as_json:
        print(json.dumps({"ok": False, "error": msg}, ensure_ascii=False))
    else:
        print("错误: " + msg, file=sys.stderr)
    return 2


# ---------- 命令 ----------

def cmd_recall(a) -> int:
    d = store_dir()
    # --index tools|projects: 直接打印对应子索引全文 (与 -q 的 fact 全文检索无关)。
    which = getattr(a, "index", None)
    if which:
        sub_name = INDEX_ALIAS.get(which)
        if sub_name is None:
            return _err(f"--index 只能是 {tuple(INDEX_ALIAS)} 之一", a.json)
        sub = d / sub_name
        if not sub.exists():
            return _err(f"子索引不存在: {sub}", a.json)
        sub_text = sub.read_text(encoding="utf-8")
        if a.json:
            print(json.dumps({"ok": True, "store": str(d), "index_file": sub_name,
                              "index": sub_text}, ensure_ascii=False, indent=2))
        else:
            print(f"# auto-memory recall --index {which}  ({sub_name})\n")
            print(sub_text.rstrip())
        return 0

    idx = index_path(d)
    if not idx.exists():
        return _err(f"MEMORY.md 不存在: {idx}", a.json)
    index_text = idx.read_text(encoding="utf-8")
    # 存在的子索引文件(供 footer 提示 codex/cursor 按需加载)
    subindexes = [n for n in SUB_INDEX_FILES if (d / n).exists()]
    matches = []
    if a.query:
        qt = tokenize(a.query)
        scored = []
        for f in list_files(d):
            body = f.read_text(encoding="utf-8")
            fm = parse_frontmatter(body)
            ft = tokenize(fm["description"] + " " + body)
            score = len(qt & ft)
            if score > 0:
                scored.append((score, f, fm, body))
        scored.sort(key=lambda x: (-x[0], x[1].name))
        for score, f, fm, body in scored[: a.max]:
            matches.append({"name": f.stem, "description": fm["description"],
                            "type": fm["type"], "score": score, "body": body})
    if a.json:
        print(json.dumps({"ok": True, "store": str(d), "index": index_text,
                          "subindexes": subindexes, "query": a.query or None,
                          "matches": matches}, ensure_ascii=False, indent=2))
    else:
        print(f"# auto-memory recall  (store: {d})\n")
        print("## 索引 (MEMORY.md — 常驻必读层)\n")
        print(index_text.rstrip())
        if a.query:
            print(f"\n## 命中 '{a.query}' 的记忆全文 ({len(matches)} 条)\n")
            if not matches:
                print("(无匹配; 仅索引已足够定位, 需要哪条用 `get --name <slug>` 取全文)")
            for m in matches:
                print(f"\n----- [{m['score']}] {m['name']} ({m['type']}) -----")
                print(m["body"].rstrip())
        if subindexes:
            print("\n## 📂 分类子索引 (按需读, 不随本文件自动加载)\n")
            for n in subindexes:
                alias = "tools" if n == "MEMORY.tools.md" else "projects"
                print(f"  - {n}  →  `recall --index {alias}` 或直接读该文件")
    return 0


def _find_section_range(lines: list[str], marker: str) -> tuple[int, int] | None:
    """定位 `## <...marker...>` 标题到下一个 `## ` 之间的行区间 [头行idx, 段末idx)。"""
    start = None
    for i, ln in enumerate(lines):
        if ln.startswith("## ") and marker in ln:
            start = i
            break
    if start is None:
        return None
    end = len(lines)
    for j in range(start + 1, len(lines)):
        if lines[j].startswith("## "):
            end = j
            break
    return (start, end)


def _subindex_header(section: str) -> str:
    """子索引文件缺失时用的头部(与 automemory_restructure 的模板一致的精简版)。"""
    heading = SECTION_HEADING.get(section, f"## {section}")
    if section == "②":
        title = "# MEMORY.tools.md — ② 工具用法 / 通用 SOP（子索引，按需加载）"
    elif section == "③":
        title = "# MEMORY.projects.md — ③ 各项目（子索引，按需加载）"
    else:
        title = f"# 子索引 {section}"
    return (f"{title}\n\n"
            "> auto-memory 子索引, **不随 MEMORY.md 自动加载**; 用 `automemory recall --index tools|projects` "
            "或直接读本文件。每条对应的 `<slug>.md` fact 仍在 memory 目录, `recall -q` 照常全文命中。\n\n"
            f"{heading}\n")


def _linked_in_any_index(d: Path, slug: str) -> str | None:
    """跨所有索引文件(根+子索引+archive)查该 slug 是否已挂链接; 返回第一个命中的文件名或 None。"""
    for name in (ROOT_INDEX, *SUB_INDEX_FILES, "MEMORY.archive.md"):
        p = d / name
        if p.exists() and f"]({slug}.md)" in p.read_text(encoding="utf-8"):
            return name
    return None


def cmd_record(a) -> int:
    d = store_dir()
    idx = index_path(d)
    if not d.exists():
        return _err(f"仓目录不存在: {d}", a.json)
    slug = a.name.strip()
    if not SLUG_RE.match(slug):
        return _err(f"name 必须是 kebab-case (小写+连字符): 收到 '{slug}'", a.json)
    if a.type not in VALID_TYPES:
        return _err(f"type 必须是 {VALID_TYPES} 之一", a.json)
    desc = a.desc.strip().replace("\n", " ")
    if not desc:
        return _err("desc 不能为空", a.json)
    dur = getattr(a, "durability", None)
    if dur and dur not in VALID_DURABILITY:
        return _err(f"durability 必须是 {VALID_DURABILITY} 之一", a.json)
    target = d / f"{slug}.md"
    if target.exists() and not a.force:
        return _err(f"记忆已存在: {slug}.md (要更新加 --force; 或换个 name)", a.json)

    # 1. 写记忆文件 (UTF-8 无 BOM, LF) —— metadata.type 管归哪区, 可选 metadata.durability 管多耐放
    body = a.body.strip("\n")
    meta = f"metadata:\n  type: {a.type}\n"
    if dur:
        meta += f"  durability: {dur}\n"
    content = f"---\nname: {slug}\ndescription: {desc}\n{meta}---\n\n{body}\n"
    target.write_text(content, encoding="utf-8", newline="\n")

    # 2. 更新对应子索引 (MOC 路由: ①→MEMORY.md, ②→MEMORY.tools.md, ③→MEMORY.projects.md)
    section = a.section or TYPE_SECTION[a.type]
    target_index_name = SECTION_INDEX_FILE.get(section, ROOT_INDEX)
    tgt_idx = d / target_index_name
    # 迁移安全: 若目标子索引尚不存在, 但根 MEMORY.md 仍保留该区(= 尚未 MOC 化的平铺态),
    # 则回落到根 MEMORY.md 插入, 不擅自新建子索引(避免迁移前把 ②/③ 拆碎、与后续 restructure 冲突)。
    if section != "①" and not tgt_idx.exists() and idx.exists():
        if _find_section_range(idx.read_text(encoding="utf-8").splitlines(), section) is not None:
            target_index_name = ROOT_INDEX
            tgt_idx = idx
    title = (a.title or slug.replace("-", " ")).strip()
    hook = (a.hook or desc).strip()
    line = f"- [{title}]({slug}.md) — {hook}"
    if len(line) > MAX_INDEX_LINE:
        keep = MAX_INDEX_LINE - (len(line) - len(hook)) - 1
        hook = hook[: max(0, keep)].rstrip() + "…"
        line = f"- [{title}]({slug}.md) — {hook}"
    index_appended = False
    index_note = ""
    created_index = False
    already_in = _linked_in_any_index(d, slug)
    if already_in:
        index_note = f"索引已有该链接(在 {already_in}), 未重复插入"
    else:
        if not tgt_idx.exists():
            # 目标子索引缺失 → 新建带头部 (① 恒有 MEMORY.md, 一般只有 ②/③ 会触发)
            tgt_idx.write_text(_subindex_header(section), encoding="utf-8", newline="\n")
            created_index = True
        text = tgt_idx.read_text(encoding="utf-8")
        lines = text.splitlines()
        rng = _find_section_range(lines, section)
        if rng is None:
            index_note = f"未在 {target_index_name} 找到区 '{section}', 索引行未插入 (需人工加): {line}"
        else:
            start, end = rng
            # 插到该区最后一个 `- ` 项之后; 无则插到标题行后
            insert_at = start + 1
            for k in range(start + 1, end):
                if lines[k].startswith("- "):
                    insert_at = k + 1
            lines.insert(insert_at, line)
            tgt_idx.write_text("\n".join(lines) + "\n", encoding="utf-8", newline="\n")
            index_appended = True
            if created_index:
                index_note = f"新建了子索引 {target_index_name}"

    # 3. 写后回读校验 (防灌坏)
    verify = {"file_parses": False, "type_ok": False, "durability_ok": False, "linked": False}
    try:
        reread = target.read_text(encoding="utf-8")
        fm = parse_frontmatter(reread)
        verify["file_parses"] = fm["name"] == slug
        verify["type_ok"] = fm["type"] == a.type
        verify["durability_ok"] = (dur or "") == fm["durability"]
        verify["linked"] = _linked_in_any_index(d, slug) is not None
    except Exception as e:
        return _err(f"写后校验读失败: {e}", a.json)

    gate = (verify["file_parses"] and verify["type_ok"]
            and verify["durability_ok"] and verify["linked"])
    result = {"ok": True, "store": str(d), "file": str(target), "slug": slug,
              "type": a.type, "durability": dur or None, "section": section,
              "index_file": target_index_name, "index_line": line,
              "index_appended": index_appended, "index_note": index_note,
              "verified": verify}
    if a.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        print(f"✅ 已写记忆: {target}")
        print(f"   类型={a.type}  耐久={dur or '(未设)'}  区={section}  →索引文件={target_index_name}")
        print(f"   索引={'已插' if index_appended else index_note or '未插'}")
        print(f"   索引行: {line}")
        print(f"   校验: 文件解析={verify['file_parses']} 类型={verify['type_ok']} "
              f"耐久={verify['durability_ok']} 已挂索引={verify['linked']}")
        if not gate:
            print("   ⚠️ 校验未全过, 请人工核对!")
    return 0 if gate else 1


def cmd_list(a) -> int:
    d = store_dir()
    if not d.exists():
        return _err(f"仓目录不存在: {d}", a.json)
    out = []
    for f in list_files(d):
        fm = parse_frontmatter(f.read_text(encoding="utf-8"))
        if a.type and fm["type"] != a.type:
            continue
        out.append({"name": f.stem, "type": fm["type"], "description": fm["description"]})
    if a.json:
        print(json.dumps({"ok": True, "store": str(d), "count": len(out), "memories": out},
                         ensure_ascii=False, indent=2))
    else:
        print(f"# auto-memory 列表 ({len(out)} 条, store: {d})\n")
        for m in out:
            print(f"  [{m['type'] or '?':9}] {m['name']}\n      {m['description']}")
    return 0


def cmd_get(a) -> int:
    d = store_dir()
    slug = a.name.strip().removesuffix(".md")
    target = d / f"{slug}.md"
    if not target.exists():
        return _err(f"记忆不存在: {slug}.md", a.json)
    body = target.read_text(encoding="utf-8")
    if a.json:
        fm = parse_frontmatter(body)
        print(json.dumps({"ok": True, "name": slug, "type": fm["type"],
                          "description": fm["description"], "body": body}, ensure_ascii=False, indent=2))
    else:
        print(body.rstrip())
    return 0


def cmd_doctor(a) -> int:
    d = store_dir()
    idx = index_path(d)
    problems = []
    files = []
    linked = orphan = broken = []
    if not d.exists():
        problems.append(f"仓目录不存在: {d}")
    elif not idx.exists():
        problems.append(f"MEMORY.md 不存在: {idx}")
    else:
        files = list_files(d)
        fileset = {f.name for f in files}
        # 索引链接 = 根 MEMORY.md + 所有子索引(MEMORY.tools.md/MEMORY.projects.md)
        #            + MEMORY.archive.md(若还没溶解) 的链接并集。
        # 被移进子索引/archive 的条目都算已登记, 不算孤儿。
        text = ""
        for name in (ROOT_INDEX, *SUB_INDEX_FILES, "MEMORY.archive.md"):
            p = d / name
            if p.exists():
                text += "\n" + p.read_text(encoding="utf-8")
        linked = index_link_files(text)   # 只算 fact-bullet 行, 排除头部 `(slug.md)` 示例
        broken = sorted(linked - fileset)
        orphan = sorted(fileset - linked)
        if broken:
            problems.append(f"索引指向不存在的文件: {broken}")
        if orphan:
            problems.append(f"文件没挂进任何索引(MEMORY.md/tools/projects): {orphan}")
    ok = not problems
    result = {"ok": ok, "store": str(d), "store_exists": d.exists(),
              "index_exists": idx.exists(), "files": len(files),
              "broken_links": broken, "orphans": orphan, "problems": problems,
              "note": "完整体检(去重/迁移/site-memo重叠)走 automemory_audit.py", "version": VERSION}
    if a.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        print(f"=== auto-memory doctor (v{VERSION}) ===")
        print(f"store: {d}  存在={d.exists()}  MEMORY.md={idx.exists()}  文件={len(files)}")
        if problems:
            for p in problems:
                print("  ✗ " + p)
        else:
            print("  ✅ 链接完整, 无孤儿/断链")
        print("  完整体检(去重/迁移候选/site-memo重叠)另跑 automemory_audit.py")
    return 0 if ok else 1


def cmd_list_tools(a) -> int:
    tools = [
        {"name": "recall", "desc": "读: 打印根 MEMORY.md + 子索引 footer; --index tools|projects 打印子索引全文; --query 附带命中 fact 全文"},
        {"name": "record", "desc": "写: 建 <slug>.md(frontmatter, 可选 --durability) + 按区路由到 MEMORY.md/tools/projects 索引, 写后回读校验"},
        {"name": "list", "desc": "列出所有记忆 name/type/description"},
        {"name": "get", "desc": "打印单条记忆全文"},
        {"name": "doctor", "desc": "健康检查: 仓存在/索引可解析/链接完整"},
        {"name": "list_tools", "desc": "本清单"},
    ]
    if a.json:
        print(json.dumps({"ok": True, "cli": "automemory", "version": VERSION,
                          "store": str(store_dir()), "tools": tools}, ensure_ascii=False, indent=2))
    else:
        print(f"automemory v{VERSION}  (store: {store_dir()})")
        for t in tools:
            print(f"  {t['name']:11} {t['desc']}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="automemory", description="跨平台读写共享 auto-memory 仓 (claude/codex/cursor 同一份)")
    p.add_argument("--json", dest="json_top", action="store_true", help="JSON 输出 (顶层, 兼容放前面)")
    sub = p.add_subparsers(dest="cmd", required=True)

    r = sub.add_parser("recall", help="读记忆 (根索引 + 子索引 footer; 可选命中全文 / 子索引全文)")
    r.add_argument("-q", "--query", help="按相关度检索, 附带命中记忆全文 (glob 搜所有 fact 文件, 与 --index 无关)")
    r.add_argument("--index", choices=tuple(INDEX_ALIAS), help="打印对应子索引全文 (tools|projects)")
    r.add_argument("--max", type=int, default=5, help="命中全文最多几条 (默认5)")
    r.add_argument("--json", action="store_true")
    r.set_defaults(func=cmd_recall)

    w = sub.add_parser("record", help="写一条记忆 (建文件 + 挂索引 + 校验)")
    w.add_argument("--name", required=True, help="kebab-case slug (= 文件名)")
    w.add_argument("--type", required=True, choices=VALID_TYPES)
    w.add_argument("--desc", required=True, help="一行摘要 (recall 相关度 + 索引 hook 默认取它)")
    w.add_argument("--body", required=True, help="记忆正文")
    w.add_argument("--title", help="索引显示标题 (默认 slug 去连字符)")
    w.add_argument("--hook", help="索引钩子句 (默认取 desc)")
    w.add_argument("--section", help="强制插入区 (①/②/③ 或标题关键词; 默认按 type)")
    w.add_argument("--durability", choices=VALID_DURABILITY,
                   help="耐久度写入 metadata.durability (axiom永不归档/workaround加冷权重/pattern/project-state; 与 type 并存)")
    w.add_argument("--force", action="store_true", help="同名已存在时覆盖更新")
    w.add_argument("--json", action="store_true")
    w.set_defaults(func=cmd_record)

    l = sub.add_parser("list", help="列出所有记忆")
    l.add_argument("--type", choices=VALID_TYPES)
    l.add_argument("--json", action="store_true")
    l.set_defaults(func=cmd_list)

    g = sub.add_parser("get", help="打印单条记忆全文")
    g.add_argument("--name", required=True)
    g.add_argument("--json", action="store_true")
    g.set_defaults(func=cmd_get)

    dr = sub.add_parser("doctor", help="健康检查")
    dr.add_argument("--json", action="store_true")
    dr.set_defaults(func=cmd_doctor)

    lt = sub.add_parser("list_tools", help="工具清单 (base 契约)")
    lt.add_argument("--json", action="store_true")
    lt.set_defaults(func=cmd_list_tools)
    return p


def main(argv=None) -> int:
    p = build_parser()
    a = p.parse_args(argv)
    # 顶层 --json(json_top) 与子命令 --json 合并
    a.json = bool(getattr(a, "json_top", False) or getattr(a, "json", False))
    return a.func(a)


if __name__ == "__main__":
    raise SystemExit(main())
