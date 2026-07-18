#!/usr/bin/env python3
"""automemory_audit.py — read-only hygiene checker for Claude's global auto-memory.

Like mindfile's audit, this SUGGESTS, never mutates. It reports drift so global
memory stays lean and well-partitioned going forward:
  - link integrity (MEMORY.md <-> files), orphans
  - dangling [[backlinks]] (INFO: convention allows placeholder / skill refs)
  - overlap with site-memo (website-ops that belong only in site-memo)
  - project-type entries living in global (candidates to migrate to a project)
  - oversized index lines, stale deleted-tool filenames
  - size summary
  - cold_split: 常驻价值打分 + 归档候选 (治 MEMORY.md 超读取上限; 只建议不动文件)

Usage:
  python automemory_audit.py            # human-readable report
  python automemory_audit.py --json     # machine-readable
  python automemory_audit.py --dir <memory_dir>   # audit a copy/fixture (default = 真仓)
It NEVER edits memory; a human/agent acts on the report.

档案感知 (MEMORY.archive.md): 冷归档索引文件不是 fact, 从待审文件集中剔除;
它登记的链接算"已登记"(与 MEMORY.md 并集), 所以被移到 archive 的条目不会被误报为孤儿/死链。
"""
from __future__ import annotations

import argparse
import datetime
import json
import re
import time
from pathlib import Path

HOME = Path.home()
MEM_DIR = HOME / ".claude" / "memory"
SITE_DIR = HOME / ".claude" / "site-memory"
INDEX_NAME = "MEMORY.md"                 # 根索引(自动加载, 只放 ① 常驻必读)
TOOLS_NAME = "MEMORY.tools.md"           # ② 子索引(按需)
PROJECTS_NAME = "MEMORY.projects.md"     # ③ 子索引(按需, 保留 ### 子节)
ARCHIVE_NAME = "MEMORY.archive.md"       # 冷归档索引 (溶解后为墓碑; 其链接仍算已登记)
# MOC 后"所有索引文件"(非 fact): 链接解析/长行检查/cold_split 都跨这几个文件。
INDEX_FILE_NAMES = (INDEX_NAME, TOOLS_NAME, PROJECTS_NAME, ARCHIVE_NAME)
MAX_LINE = 200  # index-line char cap
# 同主题疑似重复阈值(取稳, 宁缺勿滥): 两文件"内容指纹 token 集合"的重合度 >= 此值才报。
# 0.55 是经验稳值——38 文件 C(38,2)=703 对里只挑出真正高度相似的, 避免误报风暴。
DUP_OVERLAP = 0.55
DUP_MIN_TOKENS = 8  # 内容太少(<8 个特征 token)不参与去重判断, 防短文件偶然撞词

# ---- cold_split(热/冷拆分)打分常量 ----
COLD_TARGET_BYTES = 17 * 1024   # 舒适目标: 把 MEMORY.md 缩到此字节数以下(<17KB, 留足余量)
# 读取上限参考: MEMORY.md 作"常驻必读层"自动加载, 超此约 24.4KB 会被截断/超限 → 至少要压到它以下。
COLD_READLIMIT_BYTES = round(24.4 * 1024)   # ≈24986 字节
# 常驻价值分权重(分高=热=保留; 分低=冷=归档候选)。
BACKLINK_W = 100.0   # 有一个 [[反链]] 即枢纽; 权重远大于其它项 => 任何被反链的条目分都高、永不进候选
RECENCY_W = 10.0     # 新近度贡献(0..1 归一后乘此权重)
SUPERSEDE_W = 8.0    # 命中"取代/废弃"信号的冷却扣分(保守, 只微调不强制)
RECENCY_SPAN_DAYS = 365.0  # 新近度归一跨度: 越接近今天越热, 满 1 年及以上归 0
# 取代/废弃信号(保守 + 否定词护栏, 别把"没有取代/未废弃"误判成冷)
SUPERSEDE_KW = ("已废弃", "取代", "作废", "已失效", "superseded", "deprecated")
_NEG_CJK = "没未无非别不"          # 关键词前窗口出现这些字 => 视为否定, 不算取代信号
_NEG_EN = ("no", "not", "never")
# durability(耐久度)对 cold_split 的影响:
#   axiom         → 公理/铁律, 永不列归档候选(等同 ① 锁热)。
#   workaround    → 权宜/临时绕过, 加"冷"权重(更易被列为候选)。
#   pattern       → 可复用技法, 加"热"权重(降低冷分, 更抗归档)。
#   project-state → 项目状态/会腐烂, 加"冷"权重(更易被列为候选)。
#   无            → 回退现有 mtime/反链打分(不特殊处理)。
WORKAROUND_W = 5.0     # workaround 的冷权重(扣分, 让它更冷)
PATTERN_W = 3.0        # pattern 的热权重(加分, 可复用技法·略偏热·更抗归档)
PROJECT_STATE_W = 5.0  # project-state 的冷权重(扣分, 会腐烂·更易成候选; 与 WORKAROUND_W 量级一致)
# 时态失效(E): 扫 fact 正文的 valid-as-of 日期与 superseded/needs-review 标记(只报不改)
VALID_AS_OF_THRESHOLD_MONTHS = 6  # valid-as-of 超过此月数即在报告标 needs-review (可调)
_VALID_AS_OF_RE = re.compile(r"valid[_-]as[_-]of\s*:\s*(\d{4}-\d{2})", re.IGNORECASE)
_STALE_MARKER_RE = re.compile(r"\b(superseded|needs[_-]review)\b", re.IGNORECASE)


# fact-bullet 索引行: `- [标题](slug.md) ...` (排除头部 blockquote 里的 `(slug.md)` 示例 / 📂 目录 bullet)
_FACT_BULLET_RE = re.compile(r"^\s*-\s*\[.+?\]\(")


def _index_link_files(text: str) -> set[str]:
    """只从 fact-bullet 行抽 md-link 目标文件名(如 `foo.md`)。头部示例/目录 bullet 不算链接。"""
    out: set[str] = set()
    for ln in text.splitlines():
        if _FACT_BULLET_RE.match(ln):
            out.update(re.findall(r"\]\(([^)]+\.md)\)", ln))
    return out


def _site_hosts() -> list[str]:
    if not SITE_DIR.exists():
        return []
    return [f.stem for f in SITE_DIR.glob("*.md") if f.name != "INDEX.md"]


# --- finding #1 用: 内容指纹 + 重合度(复用 site_memo._line_overlap 的集合重合思路) ----

# frontmatter 字段 / 链接语法 / 常见结构词, 计相似度时是噪声, 先剔除。
_STOP = {
    "why", "how", "to", "apply", "the", "and", "for", "with", "type", "name",
    "description", "metadata", "node_type", "memory", "feedback", "project",
    "user", "reference", "originsessionid", "应用",
}


def _content_tokens(body: str) -> set[str]:
    """把 description + 正文揉成"特征 token 集合"做相似度。
    复用 site_memo 思路(集合交并比), 但跨语言: 中文按 2-gram, 英文/数字按词。
    剔 frontmatter 包裹符与停用词, 只留承载语义的部分。"""
    # 取 description 值(可能带引号) + frontmatter 之后的正文
    desc = ""
    m = re.search(r"(?m)^\s*description:\s*(.+?)\s*$", body)
    if m:
        desc = m.group(1).strip().strip('"').strip("'")
    after_fm = re.sub(r"(?s)^---.*?---\s*", "", body, count=1)
    blob = (desc + "\n" + after_fm).lower()
    # 去 wiki 链接/md 链接/行内代码, 免它们的字面干扰相似度
    blob = re.sub(r"\[\[[^\]]+\]\]", " ", blob)
    blob = re.sub(r"\[[^\]]*\]\([^)]*\)", " ", blob)
    blob = re.sub(r"`[^`]*`", " ", blob)
    toks: set[str] = set()
    # 英文词 / 数字串(>=3 字符, 滤掉 a/of 之类碎词)
    for w in re.findall(r"[a-z0-9_]{3,}", blob):
        if w not in _STOP:
            toks.add(w)
    # 中文 2-gram(连续 2 个汉字), 捕中文语义重合
    cjk = re.findall(r"[一-鿿]+", blob)
    for run in cjk:
        for i in range(len(run) - 1):
            toks.add(run[i:i + 2])
    return toks


def _set_overlap(a: set[str], b: set[str]) -> float:
    """集合重合度 = |交| / max(|a|,|b|)，同 site_memo._line_overlap 的判据(对包含关系敏感)。"""
    if not a or not b:
        return 0.0
    return len(a & b) / max(len(a), len(b))


# ---- cold_split 辅助 ----

# 索引条目行: `- [标题](slug.md) — hook`
_INDEX_LINE_RE = re.compile(r"^\s*-\s*\[(?P<title>.+?)\]\((?P<slug>[^)]+?)\.md\)\s*(?:[—-]\s*)?(?P<hook>.*)$")


def _parse_index_entries(index_text: str):
    """解析 MEMORY.md, 逐条产出索引条目及其所在区/子节。
    返回 list[dict(slug,title,hook,region,subsection,line,line_no)]。
    region ∈ {'①','②','③',''}; subsection = ③ 下的 `### 子节` 名(否则 None)。"""
    entries = []
    region = ""
    subsection = None
    for i, line in enumerate(index_text.splitlines()):
        if line.startswith("## "):
            # 顶层区标题: 认圈号定区; 换区清空子节
            if "①" in line:
                region = "①"
            elif "②" in line:
                region = "②"
            elif "③" in line:
                region = "③"
            else:
                region = ""
            subsection = None
            continue
        if line.startswith("### "):
            subsection = line[4:].strip()
            continue
        m = _INDEX_LINE_RE.match(line)
        if not m:
            continue  # 非 fact 链接行(叙事 bullet / 空行等)跳过
        entries.append({
            "slug": m.group("slug").strip(),
            "title": m.group("title").strip(),
            "hook": m.group("hook").strip(),
            "region": region,
            "subsection": subsection,
            "line": line,
            "line_no": i + 1,
        })
    return entries


def _count_backlinks(slug: str, bodies: dict[str, str]) -> int:
    """该 slug 被别的 fact 文件用 [[slug]] 反链的次数(排除自身)。有反链=枢纽=热。"""
    token = f"[[{slug}]]"
    self_name = slug + ".md"
    n = 0
    for name, body in bodies.items():
        if name == self_name:
            continue
        n += body.count(token)
    return n


def _has_supersede_signal(body: str) -> bool:
    """正文是否含"取代/废弃"冷信号 —— 保守 + 否定词护栏(别把'没有取代/未废弃'误判)。"""
    low = body.lower()
    for kw in SUPERSEDE_KW:
        k = kw.lower()
        start = 0
        while True:
            pos = low.find(k, start)
            if pos < 0:
                break
            window = low[max(0, pos - 6):pos]
            negated = any(ch in window for ch in _NEG_CJK) or any(w in window for w in _NEG_EN)
            if not negated:
                return True   # 找到一个未被否定的取代/废弃信号即算
            start = pos + len(k)
    return False


def _durability(body: str) -> str:
    """读 frontmatter 的 metadata.durability(axiom/pattern/workaround/project-state); 无则空串。"""
    m = re.search(r"(?m)^\s*durability:\s*([A-Za-z-]+)", body)
    return m.group(1).strip().lower() if m else ""


def _scan_temporal_validity(bodies: dict[str, str],
                            threshold_months: int = VALID_AS_OF_THRESHOLD_MONTHS) -> tuple[dict, dict]:
    """只读扫 fact 正文的 valid-as-of 日期与既有 superseded/needs-review 标记。
    返回:
      stale   {slug: 'YYYY-MM'} — valid-as-of 超过 threshold_months 的条目
      flagged {slug: [marker…]} — 正文已含 superseded/needs-review 标记的条目
    绝不改文件。"""
    now = datetime.datetime.now()
    stale: dict = {}
    flagged: dict = {}
    for fname, body in bodies.items():
        slug = fname[:-3]
        m = _VALID_AS_OF_RE.search(body)
        if m:
            date_str = m.group(1)  # 'YYYY-MM'
            try:
                y, mo = int(date_str[:4]), int(date_str[5:7])
                fact_date = datetime.datetime(y, mo, 1)
                delta_months = (now.year - fact_date.year) * 12 + (now.month - fact_date.month)
                if delta_months >= threshold_months:
                    stale[slug] = date_str
            except ValueError:
                pass
        markers = _STALE_MARKER_RE.findall(body)
        if markers:
            seen: set = set()
            unique: list = []
            for mk in markers:
                lmk = mk.lower()
                if lmk not in seen:
                    seen.add(lmk)
                    unique.append(mk)
            flagged[slug] = unique
    return stale, flagged


def _residency_value(backlinks: int, age_days: float, superseded: bool) -> float:
    """常驻价值分(高=热=保留, 低=冷=归档候选)。
    枢纽(有反链)权重压倒性, 保证被反链的条目分高、永不列为候选。"""
    recency_norm = max(0.0, 1.0 - age_days / RECENCY_SPAN_DAYS)
    return (BACKLINK_W * backlinks
            + RECENCY_W * recency_norm
            - (SUPERSEDE_W if superseded else 0.0))


def _cold_split(index_text: str, bodies: dict[str, str], mem_dir: Path, slugset: set[str]) -> dict:
    """计算热/冷拆分建议。只读、只建议, 不动任何文件。
    ① 区恒定热, 一条都不列; 仅 ②③ 参与评分。候选按常驻价值分升序(最冷在前),
    并给出"移到第 N 个后 MEMORY.md 约 X KB"的累计估算, 便于挑到 <17KB。"""
    now = time.time()
    current_bytes = len(index_text.encode("utf-8"))
    entries = _parse_index_entries(index_text)

    hot_locked = 0          # ① 区被锁死为热、不评分的条目数
    axiom_locked = 0        # durability=axiom 被锁死为热(等同①)、不列候选的条目数
    hub_excluded = 0        # ②③ 里有 [[反链]] = 枢纽 = 热, 排除出候选的条目数
    candidates = []
    for e in entries:
        if e["region"] == "①":
            hot_locked += 1
            continue
        if e["region"] not in ("②", "③"):
            continue  # 未归区(如顶层散行/目录段)不评, 保守不动
        slug = e["slug"]
        fname = slug + ".md"
        body = bodies.get(fname, "")
        dur = _durability(body) if body else ""
        # durability=axiom: 公理/铁律, 永不归档(等同 ① 锁热), 优先于反链/mtime 判断。
        if dur == "axiom":
            axiom_locked += 1
            continue
        fpath = mem_dir / fname
        try:
            mtime = fpath.stat().st_mtime
        except OSError:
            mtime = now  # 文件缺失(理论不该): 当作最新, 不冷
        age_days = max(0.0, (now - mtime) / 86400.0)
        backlinks = _count_backlinks(slug, bodies)
        # 枢纽护栏: 只要被别的记忆 [[反链]] 过就当热, 不列为归档候选(哪怕又老又冷)。
        if backlinks > 0:
            hub_excluded += 1
            continue
        superseded = _has_supersede_signal(body) if body else False
        value = _residency_value(backlinks, age_days, superseded)
        # durability 权重微调: workaround/project-state 扣分(更冷), pattern 加分(偏热)。
        if dur == "workaround":
            value -= WORKAROUND_W
        elif dur == "pattern":
            value += PATTERN_W
        elif dur == "project-state":
            value -= PROJECT_STATE_W
        # 该条索引行占的字节(含换行) —— 归档后对应索引文件会减少这么多
        line_bytes = len(e["line"].encode("utf-8")) + 1
        reasons = []
        reasons.append(f"区{e['region']}" + (f"/{e['subsection']}" if e["subsection"] else ""))
        reasons.append("无反链(非枢纽)" if backlinks == 0 else f"有{backlinks}反链(枢纽,偏热)")
        reasons.append(f"{age_days:.0f}天未更新")
        if dur:
            _dur_note = {
                "workaround": "(加冷权重)",
                "pattern": "(加热权重)",
                "project-state": "(加冷权重)",
            }.get(dur, "")
            reasons.append(f"durability={dur}{_dur_note}")
        if superseded:
            reasons.append("正文含取代/废弃信号")
        candidates.append({
            "slug": slug,
            "title": e["title"],
            "region": e["region"],
            "subsection": e["subsection"],
            "durability": dur or None,
            "score": round(value, 3),
            "mtime": datetime.datetime.fromtimestamp(mtime).strftime("%Y-%m-%d %H:%M:%S"),
            "mtime_days_old": round(age_days, 1),
            "backlinks": backlinks,
            "index_line_bytes": line_bytes,
            "reasons": reasons,
        })

    # 冷度排序: 常驻价值分升序(最冷在前); 同分老的在前, 再按 slug 稳定
    candidates.sort(key=lambda c: (c["score"], -c["mtime_days_old"], c["slug"]))

    # 累计估算 + 两个建议切点: ①压到读取上限(24.4KB)以下 ②压到舒适目标(17KB)以下
    removed = 0
    recommended_cut = None      # 到 <17KB 舒适目标
    cut_to_read_limit = None    # 到 <24.4KB 读取上限(治"超读取上限"的最小动作)
    for idx, c in enumerate(candidates, start=1):
        removed += c["index_line_bytes"]
        est = current_bytes - removed
        c["cumulative_removed_bytes"] = removed
        c["est_memory_md_bytes_after"] = est
        c["est_memory_md_kb_after"] = round(est / 1024.0, 1)
        if cut_to_read_limit is None and est < COLD_READLIMIT_BYTES:
            cut_to_read_limit = idx
        if recommended_cut is None and est < COLD_TARGET_BYTES:
            recommended_cut = idx

    return {
        "memory_md_bytes": current_bytes,
        "memory_md_kb": round(current_bytes / 1024.0, 1),
        "target_bytes": COLD_TARGET_BYTES,
        "target_kb": round(COLD_TARGET_BYTES / 1024.0, 1),
        "read_limit_bytes": COLD_READLIMIT_BYTES,
        "read_limit_kb": round(COLD_READLIMIT_BYTES / 1024.0, 1),
        "over_target": current_bytes >= COLD_TARGET_BYTES,
        "over_read_limit": current_bytes >= COLD_READLIMIT_BYTES,
        "hot_locked_region1": hot_locked,   # ① 区被锁为热、未评的条目数
        "axiom_locked": axiom_locked,       # durability=axiom 锁热、不列候选的条目数
        "hub_excluded": hub_excluded,       # ②③ 里有反链(枢纽)被排除出候选的条目数
        "eligible_count": len(candidates),  # ②③ 无反链、进入候选的条目数
        "cut_to_read_limit": cut_to_read_limit,  # 砍到第几个即 <24.4KB 读取上限 (最小动作)
        "recommended_cut": recommended_cut,  # 砍到第几个候选即 <17KB (None=光靠无反链候选不够)
        "archive_candidates": candidates,
    }


def audit(mem_dir: Path | None = None) -> tuple[list[tuple[str, str, str]], dict]:
    mem_dir = mem_dir or MEM_DIR
    index = mem_dir / INDEX_NAME
    archive = mem_dir / ARCHIVE_NAME
    findings: list[tuple[str, str, str]] = []
    # fact 集: 排除所有索引文件(MEMORY.md / MEMORY.tools.md / MEMORY.projects.md / MEMORY.archive.md)
    files = sorted(f for f in mem_dir.glob("*.md") if f.name not in INDEX_FILE_NAMES)
    fileset = {f.name for f in files}
    slugset = {f.stem for f in files}
    bodies = {f.name: f.read_text(encoding="utf-8") for f in files}
    # MOC 后跨"全部索引文件"读取: 每个存在的索引文件都参与链接/长行/cold_split 解析。
    index_texts = {name: (mem_dir / name).read_text(encoding="utf-8")
                   for name in INDEX_FILE_NAMES if (mem_dir / name).exists()}
    text = index_texts.get(INDEX_NAME, "")            # 根 MEMORY.md (summary 用)
    archive_text = index_texts.get(ARCHIVE_NAME, "")
    all_index_text = "\n".join(index_texts.values())  # 全部索引拼接(cold_split/链接解析用)

    # 1. link integrity —— 全部索引文件(根+子索引+archive)的链接都算"已登记"
    # 只算 fact-bullet 行(`- [..](x.md)`), 排除子索引头部 blockquote 里的 `(slug.md)` 示例与 📂 目录 bullet。
    linked = _index_link_files(all_index_text)
    broken = sorted(linked - fileset)
    orphan = sorted(fileset - linked)   # 挂进任一索引即不算孤儿
    if broken:
        findings.append(("ERROR", "broken-link", f"索引(MEMORY.md/tools/projects/archive)指向不存在的文件: {broken}"))
    if orphan:
        findings.append(("ERROR", "orphan", f"文件没挂进任何索引(MEMORY.md/tools/projects/archive): {orphan}"))

    # 2. dangling [[backlinks]] (INFO only — convention allows placeholder/skill refs)
    # A-MEM 链接健康(死链部分): [[ref]] 指向的 slug 既不在 memory 目录、也没在任何索引(MEMORY.md/archive)登记。
    # 现有约定允许占位/指 skill, 故仍 INFO; 但把"连索引都查无此名"的真死链单列出来。
    index_slugs = {s[:-3] for s in linked}   # linked 已是 fact-bullet 行的 md-link 集(去 .md)
    dangling: dict[str, list[str]] = {}
    true_dead: dict[str, list[str]] = {}
    for name, body in bodies.items():
        for ref in re.findall(r"\[\[([^\]]+)\]\]", body):
            if ref not in slugset:
                dangling.setdefault(ref, []).append(name)
                if ref not in index_slugs:  # 目录没文件、索引也没登记 = 更可疑的真死链
                    true_dead.setdefault(ref, []).append(name)
    if dangling:
        findings.append(("INFO", "dangling-backlink",
                         f"[[]] 指向非记忆条目(可能占位/指 skill): {dangling}"))
    if true_dead:
        findings.append(("INFO", "dead-wikilink",
                         f"[[]] 指向的 slug 在 memory 目录和索引都查无(疑似死链/拼错/待补): {true_dead}"))

    # 2b. A-MEM 链接健康(孤岛部分): 某 .md 既无出链 [[..]] 也无任何别处指向它 → 提示连上相关条目。
    out_links = {name: set(re.findall(r"\[\[([^\]]+)\]\]", body)) for name, body in bodies.items()}
    in_linked: set[str] = set()  # 被别的 .md 指向的 slug
    for refs in out_links.values():
        in_linked |= (refs & slugset)
    islands = sorted(
        name[:-3] for name in bodies
        if not out_links[name] and name[:-3] not in in_linked
    )
    if islands:
        findings.append(("INFO", "orphan-node",
                         f"{len(islands)} 条记忆既无出链也无入链(孤岛, 考虑用 [[..]] 连上相关条目): {islands}"))

    # 2c. 同主题疑似重复 (mem0 思想): 两个 .md 的 description+正文内容指纹高度重合 → 疑似该合并。
    # 只标不并(同 site-memo consolidate 哲学), 由人工 UPDATE 原地改其一。阈值取稳防误报。
    toks = {name: _content_tokens(body) for name, body in bodies.items()}
    dup_pairs = []
    fnames = sorted(toks)
    for i in range(len(fnames)):
        a = toks[fnames[i]]
        if len(a) < DUP_MIN_TOKENS:
            continue
        for j in range(i + 1, len(fnames)):
            b = toks[fnames[j]]
            if len(b) < DUP_MIN_TOKENS:
                continue
            ov = _set_overlap(a, b)
            if ov >= DUP_OVERLAP:
                dup_pairs.append((fnames[i], fnames[j], round(ov * 100)))
    for x, y, pct in dup_pairs:
        findings.append(("WARN", "dup-suspect",
                         f"{x} ≈ {y}(重合 {pct}%), 考虑合并(UPDATE 原地改其一, 别新建)"))

    # 3. overlap with site-memo (website-ops should live only in site-memo).
    # Only NON-project memories can be true manual-duplicates; a project memory merely
    # *mentions* a host as a deploy target, it is not a copy of that host's manual.
    hosts = _site_hosts()
    # 已人工审过、判定"通用工具 SOP 而非 host 手册副本"的, 不再当重叠告警(免钩子每会话重复打扰)
    overlap_ok = {"agent-browser-cli-playbook.md", "browser-use-real-login-state.md"}
    overlap = []
    for name, body in bodies.items():
        if name in overlap_ok:
            continue
        if re.search(r"(?m)^\s*type:\s*project", body[:400]):
            continue
        if "已收口到" in body:  # already a site-memo pointer = the resolved state, not drift
            continue
        low = body.lower()
        for h in hosts:
            if h in low:  # require the full host string; avoids generic-label (admin/console) and
                          # public-suffix (hf.space/netlify.app) false-positives — only a literal full
                          # host like "oct.agent-ai.vip" counts as a real site-memo overlap.
                overlap.append([name, h])
                break
    if overlap:
        findings.append(("WARN", "site-memo-overlap",
                         f"疑似与 site-memo 重叠(网站经验应只在 site-memo, 需人工核): {overlap}"))

    # 4. project-type entries in global (migrate candidates) — skip pointers (已迁/已收口)
    proj = []
    for name, body in bodies.items():
        if "详情已迁" in body or "已收口到" in body:
            continue  # 已是指针(详情在项目/site-memo), 不再是迁移候选
        if re.search(r"(?m)^\s*type:\s*project", body[:400]):
            proj.append(name[:-3])
    if proj:
        findings.append(("WARN", "project-in-global",
                         f"{len(proj)} 条 project 类记忆在全局(考虑迁回各项目 PROJECT_CONTEXT): {sorted(proj)}"))

    # 5. oversized index lines —— 跨全部索引文件
    longlines = []
    for name, itext in index_texts.items():
        for i, l in enumerate(itext.splitlines()):
            if len(l) > MAX_LINE:
                longlines.append((name, i + 1, len(l)))
    if longlines:
        findings.append(("WARN", "long-index-line", f"索引超 {MAX_LINE} 字的行(文件,行号,长度): {longlines}"))

    # 6. stale deleted-tool filenames
    stale = sorted(n for n in fileset if "browser-bridge" in n or "browser-relay" in n)
    if stale:
        findings.append(("INFO", "stale-filename", f"文件名含已删工具名(可改名+同步反链): {stale}"))

    # 7. temporal validity — valid-as-of 超期 + 已有 superseded/needs-review 标记(只报不改)
    stale_vao, already_flagged = _scan_temporal_validity(bodies)
    if stale_vao:
        findings.append(("WARN", "stale-valid-as-of",
                         f"{len(stale_vao)} 条 fact 的 valid-as-of 超过 "
                         f"{VALID_AS_OF_THRESHOLD_MONTHS} 个月(建议核验时效): "
                         f"{dict(sorted(stale_vao.items()))}"))
    if already_flagged:
        findings.append(("INFO", "pre-flagged-stale",
                         f"正文已含 superseded/needs-review 标记(汇总供核验): {already_flagged}"))
    temporal_review = {
        "threshold_months": VALID_AS_OF_THRESHOLD_MONTHS,
        "stale_valid_as_of": stale_vao,
        "pre_flagged_stale": already_flagged,
    }

    lines = text.splitlines()
    subindex_bytes = {name: len(t.encode("utf-8")) for name, t in index_texts.items()
                      if name in (TOOLS_NAME, PROJECTS_NAME)}
    summary = {
        "files": len(files),
        "index_bytes": len(text.encode("utf-8")),          # 根 MEMORY.md 字节(自动加载层)
        "index_lines": len(lines),
        "longest_line": max((len(l) for l in lines), default=0),
        "all_index_bytes": len(all_index_text.encode("utf-8")),   # 全部索引拼接字节
        "subindex_bytes": subindex_bytes,                  # 各子索引字节
        "index_files_present": sorted(index_texts.keys()),
        "site_memo_hosts": hosts,
        "archive_exists": archive.exists(),
        "archive_bytes": len(archive_text.encode("utf-8")),
    }
    # cold_split 跨全部索引解析 ②③ 候选(根 MEMORY.md 已只剩 ①); 字节口径 = 全部索引拼接。
    cold_split = _cold_split(all_index_text, bodies, mem_dir, slugset)
    # MOC 后: "读取上限/舒适目标"只针对**根 MEMORY.md 自动加载层**(子索引不自动加载、不占预算),
    # 覆盖 _cold_split 里按"全部索引拼接"算的预算判定, 免 MOC 后把子索引体积误报成"超读取上限"。
    _root_bytes = len(index_texts.get("MEMORY.md", "").encode("utf-8"))
    cold_split["root_memory_md_bytes"] = _root_bytes
    cold_split["over_read_limit"] = _root_bytes >= COLD_READLIMIT_BYTES
    cold_split["over_target"] = _root_bytes >= COLD_TARGET_BYTES
    if _root_bytes < COLD_READLIMIT_BYTES:
        cold_split["cut_to_read_limit"] = None
    if _root_bytes < COLD_TARGET_BYTES:
        cold_split["recommended_cut"] = None
    return findings, summary, cold_split, temporal_review


def main() -> int:
    ap = argparse.ArgumentParser(description="read-only hygiene checker for Claude auto-memory (reports, never edits)")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--dir", help="审计指定 memory 目录(副本/fixture; 默认真仓)")
    args = ap.parse_args()
    mem_dir = Path(args.dir).expanduser() if args.dir else MEM_DIR
    findings, summary, cold_split, temporal_review = audit(mem_dir)
    errors = [f for f in findings if f[0] == "ERROR"]
    if args.json:
        print(json.dumps({
            "ok": not errors,
            "findings": [{"sev": s, "kind": k, "msg": m} for s, k, m in findings],
            "summary": summary,
            "cold_split": cold_split,
            "temporal_review": temporal_review,
        }, ensure_ascii=False, indent=2))
    else:
        print("=== auto-memory 体检 (只报不改) ===")
        print(f"文件 {summary['files']} | MEMORY.md {summary['index_bytes']}字节/"
              f"{summary['index_lines']}行/最长{summary['longest_line']}字")
        print(f"索引文件: {summary['index_files_present']} | 全部索引合计 {summary['all_index_bytes']}字节"
              + (f" | 子索引字节 {summary['subindex_bytes']}" if summary['subindex_bytes'] else ""))
        if not findings:
            print("✅ 干净，无需收拾。")
        for s, k, m in findings:
            print(f"[{s}] {k}: {m}")
        # --- cold_split 段 ---
        cs = cold_split
        print("\n=== cold_split (热/冷拆分建议; 只报不改) ===")
        flag = "⚠️超读取上限" if cs["over_read_limit"] else ("⚠️超舒适目标" if cs["over_target"] else "✅在目标内")
        # 口径注: MOC 后 cold_split 按"全部索引拼接"字节评估(根 MEMORY.md 自动加载层已只剩 ①、很小);
        # 此处 flag 针对全部索引合计, 非仅根文件。
        print(f"全部索引合计 {cs['memory_md_bytes']}字节 ({cs['memory_md_kb']}KB) [根MEMORY.md={summary['index_bytes']}字节]  "
              f"读取上限<{cs['read_limit_kb']}KB / 舒适目标<{cs['target_kb']}KB  {flag}")
        print(f"① 区恒定热(不评): {cs['hot_locked_region1']}条 | axiom锁热: {cs['axiom_locked']}条 "
              f"| ②③ 枢纽(有反链,排除): {cs['hub_excluded']}条 | ②③ 候选(无反链): {cs['eligible_count']}条")
        if cs["cut_to_read_limit"] is not None:
            print(f"最小动作: 归档最冷的前 {cs['cut_to_read_limit']} 个候选 → MEMORY.md <{cs['read_limit_kb']}KB(治超读取上限)")
        elif not cs["over_read_limit"]:
            print(f"✅ 根 MEMORY.md 已在读取上限<{cs['read_limit_kb']}KB 内(MOC 两级索引), 无需为预算归档; 候选仅供子索引可选整理")
        else:
            print(f"提示: 光靠无反链候选压不到读取上限<{cs['read_limit_kb']}KB, 需缩 hook 或人工评估枢纽条目")
        if cs["recommended_cut"] is not None:
            print(f"舒适目标: 归档前 {cs['recommended_cut']} 个 → <{cs['target_kb']}KB")
        else:
            print(f"舒适目标<{cs['target_kb']}KB: 光靠无反链候选({cs['eligible_count']}条)不够, 需另缩 hook / 评估枢纽")
        print(f"归档候选(仅②③, 冷→热排序; ① 一条不列):")
        for i, c in enumerate(cs["archive_candidates"], start=1):
            mark = ""
            if i == cs["cut_to_read_limit"]:
                mark = " ← 切到这里即 <读取上限"
            elif i == cs["recommended_cut"]:
                mark = " ← 切到这里即 <舒适目标"
            print(f"  {i:2}. [{c['score']:>7.2f}] {c['slug']} ({c['region']}"
                  + (f"/{c['subsection']}" if c["subsection"] else "") + ")")
            print(f"       mtime={c['mtime']} ({c['mtime_days_old']}天) 反链={c['backlinks']} "
                  f"→ 移到此后≈{c['est_memory_md_kb_after']}KB{mark}")
            print(f"       理由: {', '.join(c['reasons'])}")
        print(f"\n结论: {'⚠️ 有可收拾项(建议非强制, 动手前人工判断)' if findings else '✅ 健康'}")
    return 1 if errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
