#!/usr/bin/env python3
# -*- coding: utf-8 -*-
r"""automemory_restructure.py — one-shot MOC (Map-of-Content) two-level restructurer
for Claude's global auto-memory.

把"平铺 MEMORY.md 单索引"升级成"MOC 两级索引":
  - MEMORY.md(根, 自动加载, 要小) = 头部 + `## ① ...常驻必读`(全部 ① 原样保留)
        + 新增 `## 📂 分类子索引` 目录段(指向两个子索引文件)。
  - MEMORY.tools.md   = 头部 + 所有 ② 工具用法/通用 SOP(含 archive 里原属②的冷条)。
  - MEMORY.projects.md= 头部 + 所有 ③ 各项目(保留 `### 子项目` 子节, 含 archive 原属③冷条)。
  - MEMORY.archive.md 溶解: 20 条按原区(②/③)并回对应子索引; archive 文件清空/删除
    (原文都在 <slug>.md fact 文件里, 无损)。

铁律:
  - **绝不动任何 `<slug>.md` fact 文件** —— 只重写/新建 MEMORY*.md 索引文件。
  - dry-run 默认(写 `.preview` 到目标目录), `--apply` 才真写、且先备份
    (MEMORY.md/tools/projects/archive → `*.bak-restructure`)。
  - 幂等: 已是新结构(根 MEMORY.md 含 `## 📂` 且无 `## ②`/`## ③`)则跳过、不写。
  - 校验: 重构前后**索引行(fact-link)总数守恒**(① + ② + ③ + archive = 根① + tools + projects),
    且**每个 slug 链接不丢**(md-link 集合相等)。

用法:
  python automemory_restructure.py --dir <memory_dir>            # dry-run, 写 .preview
  python automemory_restructure.py --dir <memory_dir> --apply    # 真写 + 先备份
  python automemory_restructure.py --dir <memory_dir> --json     # 机器可读
默认 --dir = 真仓; 但请只对副本 fixture 用 --apply 做验证。
"""
from __future__ import annotations

import argparse
import datetime
import json
import re
import sys
from pathlib import Path

try:  # Windows 控制台默认 cp1252, 强制 utf-8 免中文炸
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
except Exception:
    pass

DEFAULT_DIR = Path.home() / ".claude" / "memory"

ROOT_NAME = "MEMORY.md"
TOOLS_NAME = "MEMORY.tools.md"
PROJECTS_NAME = "MEMORY.projects.md"
ARCHIVE_NAME = "MEMORY.archive.md"

# fact-link 索引行: `- [标题](slug.md) — hook`  (narrative bullet / 目录 bullet 不匹配)
FACT_LINE_RE = re.compile(r"^\s*-\s*\[.+?\]\([^)]+?\.md\)")
# 抽所有 md-link 的 slug(含 narrative 里内嵌的), 用于"每个 slug 链接不丢"守恒校验
MDLINK_RE = re.compile(r"\]\(([^)]+\.md)\)")

TODAY = datetime.date.today().isoformat()


# ---------- section 解析 ----------

def _split_top_sections(text: str) -> tuple[str, dict[str, list[str]], list[str]]:
    """把一份索引文件切成: (preamble, {region_symbol: [body_lines]}, ordered_markers).
    region_symbol ∈ {'①','②','③','📂', 其它}; body 不含 `## ` 标题行本身。
    preamble = 第一个 `## ` 之前的所有行。"""
    lines = text.splitlines()
    preamble: list[str] = []
    sections: dict[str, list[str]] = {}
    order: list[str] = []
    cur: str | None = None
    for ln in lines:
        if ln.startswith("## "):
            sym = _region_symbol(ln)
            cur = sym
            sections.setdefault(sym, [])
            order.append(sym)
            continue
        if cur is None:
            preamble.append(ln)
        else:
            sections[cur].append(ln)
    return "\n".join(preamble), sections, order


def _region_symbol(header_line: str) -> str:
    for s in ("①", "②", "③", "📂"):
        if s in header_line:
            return s
    return header_line[3:].strip()  # fallback: 用标题文字当 key


def _parse_subsections(body_lines: list[str]) -> tuple[list[str], list[tuple[str, list[str]]]]:
    """把一个区的 body 切成 (intro_lines, [(subheader_line, [sub_body_lines])]).
    intro = 第一个 `### ` 之前的行(含 narrative/空行/fact 行)。子节顺序保留。"""
    intro: list[str] = []
    subs: list[tuple[str, list[str]]] = []
    cur_header: str | None = None
    cur_body: list[str] = []
    for ln in body_lines:
        if ln.startswith("### "):
            if cur_header is not None:
                subs.append((cur_header, cur_body))
            cur_header = ln
            cur_body = []
        elif cur_header is None:
            intro.append(ln)
        else:
            cur_body.append(ln)
    if cur_header is not None:
        subs.append((cur_header, cur_body))
    return intro, subs


def _strip_trailing_blanks(lines: list[str]) -> list[str]:
    out = list(lines)
    while out and out[-1].strip() == "":
        out.pop()
    return out


def _fact_lines(lines: list[str]) -> list[str]:
    return [ln for ln in lines if FACT_LINE_RE.match(ln)]


def _norm_subheader(h: str) -> str:
    """子节标题归一化 key: 取 `### ` 后的第一个语义词(到全角/半角括号或空白前), 用于跨文件配对。"""
    name = h[4:].strip()
    # 到第一个 括号 / 全角括号 / 空白 截断, 使 `### myproject（...）` 与 `### myproject x` 都归到 myproject
    name = re.split(r"[（(\s]", name, 1)[0]
    return name.lower()


# ---------- 计数 / 守恒 ----------

def _count_fact_lines(text: str) -> int:
    return sum(1 for ln in text.splitlines() if FACT_LINE_RE.match(ln))


def _slug_multiset(text: str) -> list[str]:
    r"""只从 fact-bullet 行(`- [..](x.md)`)抽 slug —— 排除头部 blockquote 里的
    `(slug.md)` 示例文字与目录段的 `MEMORY.tools.md` 等非 fact 引用。"""
    out: list[str] = []
    for ln in text.splitlines():
        if FACT_LINE_RE.match(ln):
            out.extend(MDLINK_RE.findall(ln))
    return out


def _narrative_sig(text: str) -> list[str]:
    """实质叙事行(非空、非 #/##/### 标题、非 > 引用/样板、非 fact-link 行)的 strip 归一。
    用于守恒校验, 补'只数 fact 行/slug'漏掉的叙事维度(防'只比数量=假绿')。"""
    out: list[str] = []
    for ln in text.splitlines():
        s = ln.strip()
        if not s or s.startswith("#") or s.startswith(">") or FACT_LINE_RE.match(ln):
            continue
        out.append(s)
    return out


# ---------- 头部模板 ----------

def _root_preamble() -> str:
    return (
        "# Memory Index\n\n"
        f"> MOC 两级索引（{TODAY} 由 automemory_restructure 生成）：本文件是**常驻必读层**、"
        "Claude 自动加载——只留 ① 常驻必读；② 工具用法 / ③ 各项目 已拆入分类子索引，"
        "**按需** `recall --index tools|projects` 或直接读对应文件。索引只留\"是什么+一个关键约束\"，详情在各 fact 文件。"
    )


def _root_moc_section() -> str:
    return (
        "## 📂 分类子索引（按需读, 不自动加载）\n\n"
        "> 下面是指向子索引的目录。Claude 只自动加载本文件的 ① 段；② / ③ 明细在子索引里，"
        "用 `automemory recall --index tools|projects` 或直接读对应文件。fact 文件都还在 memory 目录，"
        "`recall -q` 照常全文命中。\n\n"
        "- ② 工具用法 / 通用 SOP → `MEMORY.tools.md`\n"
        "- ③ 各项目 → `MEMORY.projects.md`"
    )


def _tools_header() -> str:
    return (
        "# MEMORY.tools.md — ② 工具用法 / 通用 SOP（子索引，按需加载）\n\n"
        "> auto-memory 的 **② 工具用法 / 通用 SOP** 子索引。**不随 MEMORY.md 自动加载**；"
        "用 `automemory recall --index tools` 或直接读本文件。每条对应的 `<slug>.md` fact 仍在 memory 目录，"
        "`recall -q` 照常全文命中。索引行格式 `- [标题](slug.md) — hook`。"
    )


def _projects_header() -> str:
    return (
        "# MEMORY.projects.md — ③ 各项目（子索引，按需加载）\n\n"
        "> auto-memory 的 **③ 各项目** 子索引。**不随 MEMORY.md 自动加载**；"
        "用 `automemory recall --index projects` 或直接读本文件。保留 `### 子项目` 子节结构。"
        "每条对应的 `<slug>.md` fact 仍在 memory 目录，`recall -q` 照常全文命中。"
    )


# ---------- 合并逻辑 ----------

def _merge_flat(live_body: list[str], archive_body: list[str]) -> list[str]:
    """② 区(平铺): live body 原样 + archive 的 fact 行追加到末尾。"""
    live = _strip_trailing_blanks(live_body)
    arch_facts = _fact_lines(archive_body)
    if arch_facts:
        return live + arch_facts
    return live


def _merge_subsectioned(live_body: list[str], archive_body: list[str]) -> list[str]:
    """③ 区(带 `### 子节`): 保留 live 全部子节结构; archive 的每个子节 fact 行
    并入同名 live 子节末尾, 无同名则作为新子节追加。live 内容一律 verbatim。"""
    live_intro, live_subs = _parse_subsections(live_body)
    arch_intro, arch_subs = _parse_subsections(archive_body)

    # archive intro 里的 fact 行(通常没有)并入 live intro
    live_intro_stripped = _strip_trailing_blanks(live_intro)
    arch_intro_facts = _fact_lines(arch_intro)
    if arch_intro_facts:
        live_intro_stripped = live_intro_stripped + arch_intro_facts

    # 建 live 子节的归一名 → index 映射
    live_subs2 = [[h, list(b)] for h, b in live_subs]
    name2idx = {_norm_subheader(h): i for i, (h, _b) in enumerate(live_subs2)}

    extra_subs: list[tuple[str, list[str]]] = []
    for h, b in arch_subs:
        facts = _fact_lines(b)
        if not facts:
            continue
        key = _norm_subheader(h)
        if key in name2idx:
            i = name2idx[key]
            merged = _strip_trailing_blanks(live_subs2[i][1]) + facts
            live_subs2[i][1] = merged
        else:
            extra_subs.append((h, facts))

    # 重新拼装 ③ body
    out: list[str] = []
    out.append("")  # 区标题后留空行
    intro_content = _strip_trailing_blanks(live_intro_stripped)
    # 去掉 intro 开头可能的多余空行
    while intro_content and intro_content[0].strip() == "":
        intro_content.pop(0)
    if intro_content:
        out.extend(intro_content)
        out.append("")
    for h, b in live_subs2:
        out.append(h)
        bb = _strip_trailing_blanks(b)
        # 去 body 开头空行
        while bb and bb[0].strip() == "":
            bb.pop(0)
        out.extend(bb)
        out.append("")
    for h, b in extra_subs:
        out.append(h)
        out.extend(_strip_trailing_blanks(b))
        out.append("")
    return _strip_trailing_blanks(out)


# ---------- 主流程 ----------

def build(mem_dir: Path) -> dict:
    root = mem_dir / ROOT_NAME
    archive = mem_dir / ARCHIVE_NAME
    if not root.exists():
        raise SystemExit(f"MEMORY.md 不存在: {root}")
    root_text = root.read_text(encoding="utf-8")
    archive_text = archive.read_text(encoding="utf-8") if archive.exists() else ""

    # 幂等检测: 已是新结构(根含 📂 且无 ②/③)则跳过
    _, root_secs, _ = _split_top_sections(root_text)
    already = ("📂" in root_secs) and ("②" not in root_secs) and ("③" not in root_secs)

    _, arch_secs, _ = _split_top_sections(archive_text)

    reg1 = root_secs.get("①", [])
    reg2 = root_secs.get("②", [])
    reg3 = root_secs.get("③", [])
    arc2 = arch_secs.get("②", [])
    arc3 = arch_secs.get("③", [])

    # ---- 组装 root MEMORY.md ----
    reg1_body = _strip_trailing_blanks(reg1)
    while reg1_body and reg1_body[0].strip() == "":
        reg1_body.pop(0)
    root_out = (
        _root_preamble() + "\n\n"
        + "## ① 通用经验 / 偏好 / 工作纪律（常驻必读）\n\n"
        + "\n".join(reg1_body) + "\n\n"
        + _root_moc_section() + "\n"
    )

    # ---- 组装 MEMORY.tools.md ----
    tools_body = _merge_flat(reg2, arc2)
    while tools_body and tools_body[0].strip() == "":
        tools_body.pop(0)
    tools_out = (
        _tools_header() + "\n\n"
        + "## ② 工具用法 / 通用 SOP\n\n"
        + "\n".join(tools_body) + "\n"
    )

    # ---- 组装 MEMORY.projects.md ----
    proj_body = _merge_subsectioned(reg3, arc3)
    proj_out = (
        _projects_header() + "\n\n"
        + "## ③ 各项目\n"
        + "\n".join(proj_body) + "\n"
    )

    # ---- 守恒校验 ----
    before_count = (_count_fact_lines("\n".join(reg1)) + _count_fact_lines("\n".join(reg2))
                    + _count_fact_lines("\n".join(reg3)) + _count_fact_lines(archive_text))
    after_count = (_count_fact_lines(root_out) + _count_fact_lines(tools_out)
                   + _count_fact_lines(proj_out))

    before_slugs = sorted(_slug_multiset(root_text) + _slug_multiset(archive_text))
    after_slugs = sorted(_slug_multiset(root_out) + _slug_multiset(tools_out)
                         + _slug_multiset(proj_out))
    lost = sorted(set(before_slugs) - set(after_slugs))
    gained = sorted(set(after_slugs) - set(before_slugs))
    # 重复 slug(同名出现多次)提示
    from collections import Counter
    dup_before = {s: c for s, c in Counter(before_slugs).items() if c > 1}

    # 叙事守恒(F3): 输入端全部实质叙事行必须在输出端仍能找到, 否则"零信息损失"是假保证。
    before_narr = set(_narrative_sig("\n".join(reg1) + "\n" + "\n".join(reg2)
                                     + "\n" + "\n".join(reg3) + "\n" + archive_text))
    after_narr = set(_narrative_sig(root_out + "\n" + tools_out + "\n" + proj_out))
    narrative_lost = sorted(before_narr - after_narr)

    checks = {
        "fact_lines_before": before_count,
        "fact_lines_after": after_count,
        "fact_lines_conserved": before_count == after_count,
        "slug_links_before": len(before_slugs),
        "slug_links_after": len(after_slugs),
        "slugs_lost": lost,
        "slugs_gained": gained,
        "slug_set_preserved": (not lost and not gained),
        "duplicate_slugs_before": dup_before,
        "narrative_before": len(before_narr),
        "narrative_after": len(after_narr),
        "narrative_lost": narrative_lost,
        "narrative_conserved": (not narrative_lost),
    }

    return {
        "already_restructured": already,
        "outputs": {ROOT_NAME: root_out, TOOLS_NAME: tools_out, PROJECTS_NAME: proj_out},
        "checks": checks,
        "region_counts": {
            "region1": _count_fact_lines("\n".join(reg1)),
            "region2": _count_fact_lines("\n".join(reg2)),
            "region3": _count_fact_lines("\n".join(reg3)),
            "archive": _count_fact_lines(archive_text),
        },
    }


def write_outputs(mem_dir: Path, outputs: dict[str, str], apply: bool) -> dict:
    """apply=False → 写 *.preview; apply=True → 备份原文件后真写, 并溶解 archive。"""
    written = {}
    if apply:
        # 备份所有将被覆盖/删除的索引文件
        for name in (ROOT_NAME, TOOLS_NAME, PROJECTS_NAME, ARCHIVE_NAME):
            p = mem_dir / name
            if p.exists():
                bak = mem_dir / (name + ".bak-restructure")
                bak.write_text(p.read_text(encoding="utf-8"), encoding="utf-8", newline="\n")
                written[str(bak)] = "backup"
        # 真写三文件
        for name, content in outputs.items():
            p = mem_dir / name
            p.write_text(content, encoding="utf-8", newline="\n")
            written[str(p)] = "written"
        # 溶解 archive: 清空为墓碑(保留文件名, 避免外部引用 404; 内容说明已溶解)
        arc = mem_dir / ARCHIVE_NAME
        if arc.exists():
            tomb = (
                f"# MEMORY.archive.md — 已溶解（{TODAY}）\n\n"
                "> 冷归档索引已并回 MOC 子索引：原 ② 条目 → `MEMORY.tools.md`，原 ③ 条目 → `MEMORY.projects.md`。\n"
                "> 所有 fact 文件仍在 memory 目录、`recall -q` 照常命中。本文件保留为空墓碑，可安全删除。\n"
            )
            arc.write_text(tomb, encoding="utf-8", newline="\n")
            written[str(arc)] = "dissolved"
    else:
        for name, content in outputs.items():
            p = mem_dir / (name + ".preview")
            p.write_text(content, encoding="utf-8", newline="\n")
            written[str(p)] = "preview"
    return written


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="MOC 两级索引一次性重构器 (只改 MEMORY*.md, 绝不动 fact)")
    ap.add_argument("--dir", help="memory 目录(默认真仓; 请只对副本 fixture 用 --apply)")
    ap.add_argument("--apply", action="store_true", help="真写(默认 dry-run 写 .preview); 会先备份")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args(argv)
    mem_dir = Path(a.dir).expanduser() if a.dir else DEFAULT_DIR

    res = build(mem_dir)
    checks = res["checks"]

    if res["already_restructured"]:
        msg = "已是 MOC 新结构(根含 📂 且无 ②/③), 幂等跳过, 未写任何文件。"
        if a.json:
            print(json.dumps({"ok": True, "skipped": True, "reason": msg,
                              "checks": checks}, ensure_ascii=False, indent=2))
        else:
            print("✅ " + msg)
        return 0

    conserved = (checks["fact_lines_conserved"] and checks["slug_set_preserved"]
                 and checks["narrative_conserved"])
    if not conserved:
        # 守恒失败: 绝不写, 报错让人看
        if a.json:
            print(json.dumps({"ok": False, "error": "守恒校验失败, 未写任何文件",
                              "checks": checks}, ensure_ascii=False, indent=2))
        else:
            print("✗ 守恒校验失败, 未写任何文件!")
            print(json.dumps(checks, ensure_ascii=False, indent=2))
        return 1

    written = write_outputs(mem_dir, res["outputs"], a.apply)

    result = {"ok": True, "dir": str(mem_dir), "apply": a.apply,
              "region_counts": res["region_counts"], "checks": checks, "written": written}
    if a.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        mode = "APPLY(真写+备份)" if a.apply else "DRY-RUN(写 .preview)"
        print(f"=== automemory_restructure [{mode}] dir={mem_dir} ===")
        rc = res["region_counts"]
        print(f"区计数(fact行): ①={rc['region1']} ②={rc['region2']} ③={rc['region3']} archive={rc['archive']}")
        print(f"守恒: 前={checks['fact_lines_before']}行 后={checks['fact_lines_after']}行 "
              f"→ {'✅守恒' if checks['fact_lines_conserved'] else '✗不守恒'}")
        print(f"slug链接: 前={checks['slug_links_before']} 后={checks['slug_links_after']} "
              f"丢={checks['slugs_lost']} 多={checks['slugs_gained']} "
              f"→ {'✅一个不丢' if checks['slug_set_preserved'] else '✗有丢/多'}")
        if checks["duplicate_slugs_before"]:
            print(f"⚠️ 原索引里重复出现的 slug(合并后仍按原次数保留): {checks['duplicate_slugs_before']}")
        print("写出:")
        for p, kind in written.items():
            print(f"  [{kind}] {p}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
