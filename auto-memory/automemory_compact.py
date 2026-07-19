#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""automemory_compact.py — auto-memory 热/冷拆分执行器 (仿 mindfile compress_log.py 哲学)。

治 MEMORY.md 索引超读取上限: 把"冷"条目的**索引行**从 MEMORY.md 搬到同目录
MEMORY.archive.md。**绝不动/删任何 <slug>.md fact 文件** —— 只搬索引行。
fact 文件仍在, `recall -q` 全文照样命中; 只是这些条目不再进"常驻必读"自动加载层。

安全哲学(铁律, 对齐 compress_log.py):
  - **dry-run 默认**: 只写 MEMORY.md.preview + MEMORY.archive.md.preview, 不动原文;
  - `--apply` 才真写, 且**先备份 MEMORY.md.bak-precompact**;
  - **幂等**: 已在 archive 的 slug 不重复搬;
  - **绝不删 fact 文件**; ① 区条目拒绝归档(恒定热)。

入参:
  path                仓目录(默认真仓 memory 目录; 传副本可对 fixture 演练)
  --slugs a,b,c       显式要归档的 slug 列表(由总管/用户确认后传入)
  --from-audit        读 automemory_audit 的 archive_candidates, 贪心取到 <目标 为止
  --target-kb N       --from-audit 的目标(默认 17KB; 治读取上限可传 --target-kb 24)
  --apply             真执行(默认只预览)

用法:
  python automemory_compact.py --slugs a,b,c              # dry-run 预览(安全)
  python automemory_compact.py --from-audit               # 按 audit 冷度自动挑(dry-run)
  python automemory_compact.py --slugs a,b,c --apply      # 真执行(先备份)
"""
from __future__ import annotations

import argparse
import re
import shutil
import sys
from pathlib import Path

# 同目录导入 automemory_audit(复用 index 解析 + cold_split; import 无副作用, main 仅 __main__ 跑)
sys.path.insert(0, str(Path(__file__).resolve().parent))
import automemory_audit as A  # noqa: E402

INDEX_NAME = "MEMORY.md"
ARCHIVE_NAME = "MEMORY.archive.md"
# archive 里的区标题(与 MEMORY.md 分区对齐; 只用 ②③, ① 恒定热不归档)
REGION_TITLE = {
    "②": "## ② 工具用法 / 通用 SOP（冷归档）",
    "③": "## ③ 各项目（冷归档）",
}
ARCHIVE_HEADER = (
    "# MEMORY.archive.md — 冷归档索引 (auto-memory 热/冷拆分)\n"
    "\n"
    "> 这是**按需冷归档索引**。这里每条对应的 `<slug>.md` fact 文件**仍在 memory 目录**、\n"
    "> `recall -q` 照样全文命中；只是它们的索引行已从 MEMORY.md(常驻必读层) 挪到这里、\n"
    "> **不再自动加载**，为的是给 MEMORY.md 腾出读取余量。零信息损失。\n"
    "> 由 automemory_compact.py 维护(dry-run 默认, --apply 才真写)。\n"
    "> 复原某条: 把它的索引行搬回 MEMORY.md 对应区即可。\n"
)

# archive 里识别 slug 链接行
_LINK_RE = re.compile(r"\]\(([^)]+?)\.md\)")


# ---------- archive 解析 / 渲染 ----------

def _parse_archive(text: str):
    """把已有 MEMORY.archive.md 解析成 {region: {subsection|None: [line,...]}} + 已登记 slug 集。
    保留插入顺序; 兼容首个运行(空/不存在)。"""
    buckets: dict[str, dict] = {}
    present: set[str] = set()
    region = None
    subsection = None
    for line in text.splitlines():
        s = line.strip()
        if line.startswith("## "):
            region = "②" if "②" in line else ("③" if "③" in line else None)
            subsection = None
            if region:
                buckets.setdefault(region, {})
            continue
        if line.startswith("### "):
            subsection = line[4:].strip()
            if region:
                buckets.setdefault(region, {}).setdefault(subsection, [])
            continue
        if region and s.startswith("- ") and _LINK_RE.search(line):
            buckets.setdefault(region, {}).setdefault(subsection, []).append(line.rstrip())
            m = _LINK_RE.search(line)
            if m:
                present.add(m.group(1))
    return buckets, present


def _render_archive(buckets: dict[str, dict]) -> str:
    """从 buckets 渲染完整 archive 文本(幂等: 每区/子节唯一标题, 不堆叠)。"""
    parts = [ARCHIVE_HEADER.rstrip()]
    for region in ("②", "③"):
        b = buckets.get(region)
        if not b:
            continue
        # 该区有任何内容才写区标题
        has_any = any(lines for lines in b.values())
        if not has_any:
            continue
        parts.append("")
        parts.append(REGION_TITLE[region])
        # 顶层(无子节)先写
        top = b.get(None, [])
        if top:
            parts.append("")
            parts.extend(top)
        # 再各子节(仅 ③ 有), 保留插入顺序
        for sub, lines in b.items():
            if sub is None or not lines:
                continue
            parts.append("")
            parts.append(f"### {sub}")
            parts.append("")
            parts.extend(lines)
    return "\n".join(parts) + "\n"


# ---------- 主流程 ----------

def _select_from_audit(mem_dir: Path, target_bytes: int):
    """按 audit 的 cold_split 冷度顺序贪心取 slug, 到累计估算 < target_bytes 为止。
    取不到(候选不足)则取全部候选。返回 (slugs, reached, cold_split)。"""
    _findings, _summary, cs, _temporal = A.audit(mem_dir)  # audit() 2026-07-18 起返回 4 值(+temporal_review)
    chosen = []
    reached = False
    for c in cs["archive_candidates"]:
        chosen.append(c["slug"])
        if c["est_memory_md_bytes_after"] < target_bytes:
            reached = True
            break
    return chosen, reached, cs


def compact(path: str | None, slugs: list[str] | None, from_audit: bool,
            target_kb: float, apply: bool) -> int:
    mem_dir = Path(path).expanduser() if path else A.MEM_DIR
    index = mem_dir / INDEX_NAME
    archive = mem_dir / ARCHIVE_NAME
    if not index.exists():
        print(f"[skip] 未找到 MEMORY.md: {index}")
        return 1

    target_bytes = round(target_kb * 1024)
    if from_audit:
        want, reached, cs = _select_from_audit(mem_dir, target_bytes)
        print(f"[from-audit] 冷度候选 {len(cs['archive_candidates'])} 个; "
              f"贪心取 {len(want)} 个到 <{target_kb}KB {'达成' if reached else '(候选不足,取全部,未达标)'}")
    else:
        want = list(dict.fromkeys((s.strip().removesuffix('.md') for s in (slugs or []) if s.strip())))
    if not want:
        print("[skip] 没有要归档的 slug(用 --slugs a,b,c 或 --from-audit)。")
        return 1

    index_text = index.read_text(encoding="utf-8")
    archive_text = archive.read_text(encoding="utf-8") if archive.exists() else ""

    # 解析 MEMORY.md 索引条目; 建 slug -> [entry,...] (②③ 才可归档)
    entries = A._parse_index_entries(index_text)
    by_slug: dict[str, list] = {}
    for e in entries:
        by_slug.setdefault(e["slug"], []).append(e)

    # 解析已有 archive(幂等去重)
    arch_buckets, already = _parse_archive(archive_text)

    to_move = []       # (slug, region, subsection, line, line_no)
    skipped = {"not_in_index": [], "region1_refused": [], "already_archived": [],
               "no_region": [], "axiom_refused": [], "hub_warned": []}
    # 加载 fact 正文(排除 MEMORY* 索引), 供 axiom/反链判定(对齐 audit 的"axiom 等同①锁热")
    bodies = {f.name: f.read_text(encoding="utf-8", errors="replace")
              for f in mem_dir.glob("*.md") if not f.name.startswith("MEMORY")}
    for slug in want:
        if slug in already:
            skipped["already_archived"].append(slug)
            continue
        ents = by_slug.get(slug)
        if not ents:
            skipped["not_in_index"].append(slug)
            continue
        for e in ents:
            if e["region"] == "①":
                skipped["region1_refused"].append(slug)   # ① 恒定热, 拒绝归档
                continue
            if e["region"] not in ("②", "③"):
                skipped["no_region"].append(slug)
                continue
            # F2: durability=axiom 等同①锁热, 铁律永不归档(即便人工 --slugs 指名也硬拒)
            if A._durability(bodies.get(slug + ".md", "")) == "axiom":
                skipped["axiom_refused"].append(slug)
                continue
            # 反链枢纽(被别的记忆 [[引用]]): 告警但不拦, 交用户判断
            if A._count_backlinks(slug, bodies) > 0:
                skipped["hub_warned"].append(slug)
            to_move.append((slug, e["region"], e["subsection"], e["line"], e["line_no"]))

    if not to_move:
        print("[skip] 无可归档条目(可能都已在 archive / 不在索引 / 属①区)。")
        _print_skips(skipped)
        return 1

    # 1) 从 MEMORY.md 删掉这些行(按 line_no 精确删, 不碰别的)
    remove_lnos = {lno for (_s, _r, _sub, _l, lno) in to_move}
    old_lines = index_text.splitlines()
    new_lines = [ln for i, ln in enumerate(old_lines, start=1) if i not in remove_lnos]
    trailing = "\n" if index_text.endswith("\n") else ""
    new_index_text = "\n".join(new_lines) + trailing

    # 2) 把删掉的行并入 archive buckets(幂等: 已在 archive 的不重复; 保持区/子节结构)
    for slug, region, subsection, line, _lno in to_move:
        b = arch_buckets.setdefault(region, {}).setdefault(subsection, [])
        if not any(_LINK_RE.search(x) and _LINK_RE.search(x).group(1) == slug for x in b):
            b.append(line.rstrip())
    new_archive_text = _render_archive(arch_buckets)

    # F1 护栏: _parse_archive 只采集"②③区内的 fact-link 行", 会丢弃区标题前的游离 fact 行
    # 与一切叙事散文; 整体重渲染会静默丢失它们, 且被覆盖的 archive(下面)无从恢复。
    # 宁可中止也不静默丢: 检测旧 archive 里"会被丢的实质内容", 有则拒跑。
    def _sig_slugs(t):
        return {m.group(1) for m in (_LINK_RE.search(l) for l in t.splitlines()) if m}
    _hdr_lines = {l.strip() for l in ARCHIVE_HEADER.splitlines() if l.strip()}
    _lost_slugs = sorted(_sig_slugs(archive_text) - _sig_slugs(new_archive_text))
    _dropped_prose = [l.rstrip() for l in archive_text.splitlines()
                      if l.strip() and l.strip() not in _hdr_lines
                      and not l.lstrip().startswith("#")
                      and not l.lstrip().startswith(">")
                      and not _LINK_RE.search(l)]
    if _lost_slugs or _dropped_prose:
        print(f"[abort] {archive.name} 含 compact 无法安全保留的内容, 已中止(未改任何文件):")
        if _lost_slugs:
            print(f"          - {len(_lost_slugs)} 条区标题前的游离 fact 行会丢失: {_lost_slugs[:5]}")
        if _dropped_prose:
            print(f"          - {len(_dropped_prose)} 行叙事/散文会被整体重渲染丢弃, 例:")
            for _l in _dropped_prose[:4]:
                print(f"              {_l[:100]}")
        print("        请改用 restructure(能兜住叙事)或人工处理 archive 后再跑 compact。")
        return 1

    old_bytes = len(index_text.encode("utf-8"))
    new_bytes = len(new_index_text.encode("utf-8"))

    if not apply:
        idx_prev = index.with_suffix(index.suffix + ".preview")
        arc_prev = archive.with_suffix(archive.suffix + ".preview")
        idx_prev.write_text(new_index_text, encoding="utf-8", newline="\n")
        arc_prev.write_text(new_archive_text, encoding="utf-8", newline="\n")
        print(f"[DRY-RUN] 将归档 {len(to_move)} 条索引行(fact 文件一律不动)。")
        print(f"          MEMORY.md {old_bytes}字节 → 约 {new_bytes}字节 ({old_bytes/1024:.1f}KB → {new_bytes/1024:.1f}KB)")
        print(f"          预览已写: {idx_prev.name} + {arc_prev.name}  (原文件未动; 满意后加 --apply)")
        for slug, region, subsection, _l, _lno in to_move:
            print(f"            - {slug} (区{region}" + (f"/{subsection}" if subsection else "") + ")")
        _print_skips(skipped)
        return 0

    # --apply: 先备份 MEMORY.md 与 archive, 再真写(fact 文件绝不碰)
    bak = index.with_suffix(index.suffix + ".bak-precompact")
    shutil.copy2(index, bak)
    arc_bak_name = "(无 archive, 首次)"
    if archive.exists():
        arc_bak = archive.with_suffix(archive.suffix + ".bak-precompact")
        shutil.copy2(archive, arc_bak)
        arc_bak_name = arc_bak.name
    index.write_text(new_index_text, encoding="utf-8", newline="\n")
    archive.write_text(new_archive_text, encoding="utf-8", newline="\n")
    print(f"[done] 归档 {len(to_move)} 条索引行 → {archive.name}; "
          f"MEMORY.md {old_bytes/1024:.1f}KB → {new_bytes/1024:.1f}KB; 备份 {bak.name} + {arc_bak_name}")
    print("       (fact <slug>.md 文件一个没动; recall -q 照样全文命中)")
    for slug, region, subsection, _l, _lno in to_move:
        print(f"         - {slug} (区{region}" + (f"/{subsection}" if subsection else "") + ")")
    _print_skips(skipped)
    return 0


def _print_skips(skipped: dict):
    if skipped["already_archived"]:
        print(f"       [幂等跳过] 已在 archive: {skipped['already_archived']}")
    if skipped["not_in_index"]:
        print(f"       [跳过] 不在 MEMORY.md 索引(拼错/已归档?): {skipped['not_in_index']}")
    if skipped["region1_refused"]:
        print(f"       [拒绝] ①区恒定热、不归档: {skipped['region1_refused']}")
    if skipped["no_region"]:
        print(f"       [跳过] 未归到②③区: {skipped['no_region']}")
    if skipped.get("axiom_refused"):
        print(f"       [拒绝] durability=axiom 铁律恒热、不归档: {skipped['axiom_refused']}")
    if skipped.get("hub_warned"):
        print(f"       [告警] 被[[反链]]的枢纽条仍将归档(如不该归请从 --slugs 移除): {skipped['hub_warned']}")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="auto-memory 热/冷拆分执行器(默认 dry-run, 绝不删 fact 文件)")
    ap.add_argument("path", nargs="?", default=None, help="仓目录(默认真仓 memory 目录)")
    ap.add_argument("--slugs", help="显式归档的 slug 列表, 逗号分隔")
    ap.add_argument("--from-audit", action="store_true", help="按 audit 冷度自动挑到 <目标 为止")
    ap.add_argument("--target-kb", type=float, default=17.0, help="--from-audit 目标KB(默认17)")
    ap.add_argument("--apply", action="store_true", help="真执行(默认只预览)")
    a = ap.parse_args(argv)
    if not a.slugs and not a.from_audit:
        ap.error("需要 --slugs a,b,c 或 --from-audit 之一")
    slugs = a.slugs.split(",") if a.slugs else None
    return compact(a.path, slugs, a.from_audit, a.target_kb, a.apply)


if __name__ == "__main__":
    raise SystemExit(main())
