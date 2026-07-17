#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""compress_log.py — LOG.md 历史压缩归档(对齐 GA L4 + mindfile "删记忆高风险、只建议" 哲学)。

默认只预览(写 .preview, 不动原文)。当 LOG.md 超阈值时, 把较旧条目压成"一行摘要指针",
原文整段搬进同目录 LOG.archive.md(anchor 幂等), 保留最近 N 条。绝不删原文; --apply 才
真写, 且先备份 LOG.md.bak-precompress。

两项增强(2026-07):
  ② 类型感知保留 —— 条目标题可带类型标签 `## <日期> [决策]/[坑]/[里程碑]/[流水] ...`:
     · [决策]/[坑]/[里程碑] → 归档后仍留"指针"(这是 why 价值);
     · 显式 [流水]          → 归档后不留指针(原文仍在 archive, 索引不留行 = 治"太长");
     · 无标签(默认当流水)   → 保守: 仍留指针(不确定重要性, 别因猜测丢索引)。
  ① 按纪元(月)分组 —— "已归档历史"区里指针按 `### <YYYY-MM>`(从条目日期解析)分子节,
     索引有结构可折叠; --apply 时把老的平铺指针迁进对应月份子节, 解析不出的归 `### 未分期`。
     已有的"反思综述"叙事(某月子节里的散文)会被原样保留, 不被指针重排冲掉。

用法:
  python compress_log.py <项目根 或 LOG.md路径>          # dry-run 预览(安全, 默认)
  python compress_log.py <...> --apply                   # 真执行(先备份)
"""
import re, os, sys, shutil, hashlib, argparse

MAX_LINES = 1200          # 行数阈值(任一超即触发)
MAX_BYTES = 60 * 1024     # 字节阈值
KEEP_RECENT = 30          # 保留最近 N 条不动(对应 GA recent<2h 护栏)
ENTRY_RE = re.compile(r'^##\s+\S', re.M)   # 按 ## 标题分条; LOG.md 规范用 ## 日期标题
                                           # (### 月份子节不匹配: `##` 后是 `#` 非空白)

PTR_TITLE = "## 已归档历史(摘要指针)"
UNDATED = "未分期"                          # 日期解析不出时的兜底月份桶
PTR_HINT_THRESHOLD = 40                     # 反思信号: 总指针数超此值 → 提示
UNCOVERED_MONTH_THRESHOLD = 3              # 反思信号: 未被叙事覆盖的月份 ≥ 此值 → 提示

MONTH_HEAD_RE = re.compile(r'^###\s+(\S+)')            # 月份子节标题 `### 2026-06`
YM_RE = re.compile(r'(\d{4})-(\d{2})')                 # 从文本抽 YYYY-MM
TYPE_RE = re.compile(r'[\[【]\s*(决策|坑|里程碑|流水)\s*[\]】]')  # 标题里的类型标签


def _resolve_log(path):
    return os.path.join(path, "LOG.md") if os.path.isdir(path) else path


def _split_entries(text):
    ms = list(ENTRY_RE.finditer(text))
    if not ms:
        return text, []
    head = text[:ms[0].start()]
    entries = []
    for i, m in enumerate(ms):
        end = ms[i + 1].start() if i + 1 < len(ms) else len(text)
        nl = text.find('\n', m.start())
        title = text[m.start():(nl if nl != -1 else end)].strip()
        entries.append((title, text[m.start():end]))
    return head, entries


def _anchor(seg):
    return hashlib.sha1(seg.encode('utf-8')).hexdigest()[:8]


def _summary(title, seg):
    first = next((l.strip() for l in seg.splitlines()[1:] if l.strip()), '')
    return title.lstrip('# ').strip() + (' — ' + first[:90] if first else '')


def _archive_ids(p):
    if not os.path.exists(p):
        return set()
    txt = open(p, encoding='utf-8', errors='replace').read()
    return set(re.findall(r'<!-- anchor:([0-9a-f]{8}) -->', txt))


def _is_pointer_line(line):
    return line.strip().startswith("- [archived:")


def _entry_type(title):
    """标题里显式的类型标签: '决策'|'坑'|'里程碑'|'流水'; 无标签返回 '' (默认当流水但保守处理)。"""
    m = TYPE_RE.search(title)
    return m.group(1) if m else ''


def _keep_pointer(title):
    """归档后是否保留索引指针。
    只有显式 [流水] 丢指针; [决策]/[坑]/[里程碑] 及无标签(默认流水, 保守)都保留。"""
    return _entry_type(title) != '流水'


def _month_of(text):
    """从文本解析 'YYYY-MM'; 解析不出返回 UNDATED。"""
    m = YM_RE.search(text)
    return f"{m.group(1)}-{m.group(2)}" if m else UNDATED


def _parse_pointer_section(seg):
    """把已有的"已归档历史"块解析成 {month: {'narr':[...], 'ptrs':[...]}} + 有序月份列表。
    同时兼容旧的平铺列表(无 ### 月份标题)与新的分组格式; 保留月份子节里的叙事(反思综述)。"""
    buckets, order = {}, []

    def bucket(month):
        if month not in buckets:
            buckets[month] = {'narr': [], 'ptrs': []}
            order.append(month)
        return buckets[month]

    cur = None   # 当前月份标题; None = 尚未进入任何 ### 子节的平铺区
    for line in seg.splitlines():
        if line.strip() == PTR_TITLE.strip():
            continue
        mh = MONTH_HEAD_RE.match(line)
        if mh:
            cur = mh.group(1)
            bucket(cur)                      # 即使空月份标题也登记, 保留结构
            continue
        if _is_pointer_line(line):
            month = cur if cur is not None else _month_of(line)  # 平铺指针 → 从文本推月份
            bucket(month)['ptrs'].append(line.rstrip())
        elif line.strip():                   # 叙事/反思综述散文(非空、非指针、非标题)
            bucket(cur if cur is not None else UNDATED)['narr'].append(line.rstrip())
        # 空行丢弃, 渲染时重新加
    return buckets, order


def _render_pointer_section(buckets, order):
    """渲染分组指针区。月份按时间升序、UNDATED 垫底; 空桶(无叙事无指针)略过。"""
    def key(m):
        return (1, m) if m == UNDATED else (0, m)

    parts = [PTR_TITLE, ""]
    for m in sorted(order, key=key):
        b = buckets[m]
        if not b['narr'] and not b['ptrs']:
            continue
        parts.append(f"### {m}")
        parts.append("")
        body = list(b['narr'])
        if b['narr'] and b['ptrs']:
            body.append("")                  # 叙事与指针之间留一空行
        body.extend(b['ptrs'])
        parts.extend(body)
        parts.append("")
    while parts and parts[-1] == "":
        parts.pop()
    return "\n".join(parts)


def _reflection_hint(buckets, order):
    """压缩输出末尾: 指针总数 > 阈值 或 未被叙事覆盖的月份数 ≥ 阈值 → 提示跑反思固化(只提示不自动做)。"""
    total = sum(len(buckets[m]['ptrs']) for m in order)
    uncovered = sum(1 for m in order
                    if YM_RE.fullmatch(m) and buckets[m]['ptrs'] and not buckets[m]['narr'])
    if total > PTR_HINT_THRESHOLD or uncovered >= UNCOVERED_MONTH_THRESHOLD:
        print("[hint] 索引偏长, 建议跑反思固化 SOP(见 SKILL.md)把老纪元压成叙事")


def compress(raw_path, apply=False):
    log = _resolve_log(raw_path)
    if not os.path.isfile(log):
        print(f"[skip] 未找到 LOG.md: {log}")
        return
    text = open(log, encoding='utf-8', errors='replace').read()
    n_lines, n_bytes = text.count('\n') + 1, len(text.encode('utf-8'))
    if n_lines < MAX_LINES and n_bytes < MAX_BYTES:
        print(f"[skip] 未超阈值 ({n_lines}行/{n_bytes//1024}KB < {MAX_LINES}行/{MAX_BYTES//1024}KB), 无需压缩。")
        return
    head, all_entries = _split_entries(text)
    # 剔除上一轮生成的"已归档历史(摘要指针)"目录块: 它不是真历史条目、不参与再归档,
    # 其已有指针/叙事并入本轮新目录(解析成月份桶) -> 避免反复 apply 堆叠"指针的指针"。
    prev_buckets, prev_order = {}, []
    entries = []
    for title, seg in all_entries:
        if title.strip().startswith(PTR_TITLE):
            prev_buckets, prev_order = _parse_pointer_section(seg)
        else:
            entries.append((title, seg))
    if len(entries) <= KEEP_RECENT:
        print(f"[skip] 仅 {len(entries)} 条真历史(## 标题) ≤ 保留数 {KEEP_RECENT}, 不压"
              f"{'(条目可能不是 ## 标题, 检查 LOG 格式)' if not entries else ''}。")
        return
    old, recent = entries[:-KEEP_RECENT], entries[-KEEP_RECENT:]
    archive = os.path.join(os.path.dirname(log), "LOG.archive.md")
    done = _archive_ids(archive)

    # 本轮月份桶 = 上一轮解析结果的深拷贝(可再追加新指针)。
    buckets = {m: {'narr': list(prev_buckets[m]['narr']),
                   'ptrs': list(prev_buckets[m]['ptrs'])} for m in prev_order}
    order = list(prev_order)

    def bucket(month):
        if month not in buckets:
            buckets[month] = {'narr': [], 'ptrs': []}
            order.append(month)
        return buckets[month]

    to_arch, n_ptr_kept, n_flow_dropped = [], 0, 0
    for title, seg in old:
        aid = _anchor(seg)
        if aid in done:
            continue
        to_arch.append((aid, seg))            # 原文一律进 archive(只增不减, 与类型无关)
        if _keep_pointer(title):
            ptr = f"- [archived:{aid}] {_summary(title, seg)} → LOG.archive.md#{aid}"
            bucket(_month_of(title))['ptrs'].append(ptr)
            n_ptr_kept += 1
        else:
            n_flow_dropped += 1               # 显式 [流水]: 原文已进 archive, 索引不留指针
    if not to_arch:
        print("[skip] 旧条目均已归档过, 无新增可压。")
        return
    ptr_section = _render_pointer_section(buckets, order)
    new_log = (head.rstrip() + "\n\n" + ptr_section
               + "\n\n" + "".join(seg for _, seg in recent))
    new_bytes = len(new_log.encode('utf-8'))
    if not apply:
        prev = log + ".preview"
        open(prev, 'w', encoding='utf-8').write(new_log)
        print(f"[DRY-RUN] 可归档 {len(to_arch)} 条旧记录、保留近 {len(recent)} 条。")
        print(f"          留指针 {n_ptr_kept} 条 / 显式[流水]不留指针 {n_flow_dropped} 条(原文仍进 archive)。")
        print(f"          {n_lines}行/{n_bytes//1024}KB  →  约 {new_log.count(chr(10))+1}行/{new_bytes//1024}KB")
        print(f"          预览已写: {prev}   (原 LOG.md 未动; 满意后加 --apply 执行)")
        _reflection_hint(buckets, order)
        return
    shutil.copy2(log, log + ".bak-precompress")
    with open(archive, 'a', encoding='utf-8') as f:
        for aid, seg in to_arch:
            f.write(f"\n<!-- anchor:{aid} -->\n{seg.rstrip()}\n")
    open(log, 'w', encoding='utf-8').write(new_log)
    print(f"[done] 归档 {len(to_arch)} 条 → {archive}; 留指针 {n_ptr_kept}/丢流水指针 {n_flow_dropped}; "
          f"LOG.md 压到 {new_bytes//1024}KB; 备份 {log}.bak-precompress")
    _reflection_hint(buckets, order)


if __name__ == '__main__':
    ap = argparse.ArgumentParser(description="LOG.md 历史压缩归档(默认 dry-run, 绝不删原文)")
    ap.add_argument("path", help="项目根目录 或 LOG.md 路径")
    ap.add_argument("--apply", action="store_true", help="真执行(默认只预览)")
    a = ap.parse_args()
    compress(a.path, apply=a.apply)
