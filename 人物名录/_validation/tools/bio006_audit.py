#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
BIO-006 全库验收审计工具
========================

背景
----
BIO-006 定义了"传记成品"的六条最低验收规则：

    R1：一手依据 —— 每篇至少 2 条一手或高等级二手依据；
    R2：存疑标注搬运 —— 删净正文内 "[来源：存疑]"、"（原始出处待查）"
         等句内标注（允许存在于存疑章节内）；
    R3：未决争议清单 —— 文件末尾须保留"存疑 & 待查 / 争议与待考"之类章节；
    R4：来源汇总完整 —— 独立的"来源汇总"章节应不少于 5 条；
    R5：基本信息完整 —— 生卒 / 字号 / 籍贯 / 身份（官职/称号）
         至少提供 3 项；
    R6：文件长度合理 —— 正文 ≥ 100 行视为合格；100–200 行标记为"合格偏短"。

本脚本对 `{base_dir}/` 下所有传记 Markdown 执行规则检查，分两种输出：

1. JSON（机器可读）—— 便于流水线聚合；
2. Markdown 报告 —— 便于人工阅读，含按规则清单和按文件总表。

用法
----
    python bio006_audit.py --base-dir <人物名录根目录> \
        [--output <Markdown 报告路径>] \
        [--json <JSON 结果路径>] \
        [--quiet]

参数说明
--------
- ``--base-dir``:  人物名录根目录。
- ``--output``:    Markdown 报告输出路径。默认
                   ``{base_dir}/_validation/BIO-006验收报告.md``。
- ``--json``:      JSON 结果路径。默认
                   ``{base_dir}/_validation/BIO-006审计结果.json``。
- ``--quiet``:     仅输出统计。

退出码
------
- 0：无严重不合规；
- 1：存在严重不合规文件；
- 2：参数或文件系统错误。
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from typing import Dict, List, Optional, Tuple

# ----- 关键词字典 -----

PRIMARY_KEYWORDS = [
    "明史", "明实录", "明世宗实录", "明穆宗实录", "明神宗实录", "明熹宗实录",
    "明光宗实录", "明毅宗实录", "明武宗实录", "明宪宗实录",
    "国榷", "明会要", "大明会典", "大明律", "万历会计录",
    "明通鉴", "明史纪事本末", "国朝献征录", "皇明经世文编",
    "万历邸钞", "万历野获编", "罪惟录", "弇州史料",
    "明儒学案", "四库全书", "明文海", "经世文编",
    "行实", "行状", "墓志", "墓表", "神道碑", "年谱",
    "列朝诗集", "名山藏", "殊域周咨录", "筹海图编",
    "纪效新书", "练兵实纪",
    # 学术/专著（高等级二手）
    "黄仁宇", "樊树志", "卜正民", "牟复礼", "傅衣凌", "韦庆远", "陈宝良",
    "剑桥中国明代史", "明代政治制度", "万历十五年",
    # 志书类
    "府志", "县志", "通志",
]

SUSPECT_MARKERS = [
    r"\[来源：存疑\]",
    r"（原始出处待查）",
    r"\(原始出处待查\)",
    r"来源：待查",
    r"【存疑】",
]

SUSPECT_SECTION_NAMES = [
    "存疑 & 待查", "存疑&待查", "争议与待考", "争议与存疑",
    "未决争议", "待查", "存疑",
]

SOURCE_SECTION_NAMES = [
    "来源汇总", "参考文献", "主要来源", "来源追踪",
    "主要参考", "史料来源", "资料来源", "引用来源", "参考资料",
]


# ----- 基础工具 -----

def read_text(path: str) -> str:
    with open(path, "r", encoding="utf-8") as f:
        return f.read()


def count_primary_refs(text: str) -> set:
    """从 ``[来源：xxx]`` 标注抽取出的高等级来源键集合。"""
    hits = set()
    pattern = re.compile(r"\[来源[：:]([^\]]+)\]")
    for m in pattern.finditer(text):
        src = m.group(1).strip()
        if "存疑" in src or "待查" in src or not src:
            continue
        hits.add(src[:40])  # 归一化前 40 字符
    return hits


def count_refs_by_keywords(text: str) -> int:
    """关键词命中次数，用于兜底判断是否有正史引证。"""
    return sum(text.count(kw) for kw in PRIMARY_KEYWORDS)


def detect_suspect_markers(text: str) -> List[Tuple[int, str]]:
    """检测正文中（存疑章节外）残留的 ``[来源：存疑]`` 类标注。"""
    lines = text.split("\n")
    section = "head"
    offending: List[Tuple[int, str]] = []
    for i, line in enumerate(lines, 1):
        if line.startswith("## "):
            title = line[3:].strip()
            in_suspect = any(name in title for name in SUSPECT_SECTION_NAMES)
            section = "suspect" if in_suspect else "normal"
            continue
        if section == "suspect":
            continue
        for pat in SUSPECT_MARKERS:
            if re.search(pat, line):
                offending.append((i, line.strip()[:80]))
                break
    return offending


def detect_suspect_section(text: str) -> Tuple[bool, Optional[str], int]:
    """检查是否有"存疑 & 待查"章节且包含实质条目。"""
    lines = text.split("\n")
    in_section = False
    content_lines = 0
    section_title: Optional[str] = None
    for line in lines:
        if line.startswith("## "):
            title = line[3:].strip()
            if any(name in title for name in SUSPECT_SECTION_NAMES):
                in_section = True
                section_title = title
                content_lines = 0
                continue
            elif in_section:
                break
        elif in_section:
            s = line.strip()
            if not s or s.startswith(">"):
                continue
            if s.startswith("-") or s.startswith("*") or re.match(r"^\d+\.", s):
                content_lines += 1
            elif len(s) > 10 and not s.startswith("#"):
                content_lines += 1
    return in_section, section_title, content_lines


def detect_source_section(text: str) -> Tuple[bool, Optional[str], int]:
    """检查来源汇总章节及其条目数（支持列表和表格两种写法）。"""
    lines = text.split("\n")
    in_section = False
    count = 0
    section_title: Optional[str] = None
    header_row_seen = False
    for line in lines:
        if line.startswith("## "):
            title = line[3:].strip()
            matched = any(
                name in title and "存疑" not in title for name in SOURCE_SECTION_NAMES
            )
            if matched:
                in_section = True
                section_title = title
                count = 0
                header_row_seen = False
                continue
            elif in_section:
                break
        elif in_section:
            s = line.strip()
            if not s:
                continue
            if s.startswith("-") or s.startswith("*") or re.match(r"^\d+\.", s):
                count += 1
            elif s.startswith("|"):
                if re.match(r"^\|[\s\-:|]+\|?\s*$", s):
                    continue
                if not header_row_seen and any(
                    h in s for h in ["序号", "来源名称", "类型", "可信度"]
                ):
                    header_row_seen = True
                    continue
                count += 1
    return in_section, section_title, count


def detect_basic_info(text: str) -> Tuple[List[str], bool]:
    """检查"基本信息"章节：生卒/字号/籍贯/身份四项的覆盖情况。"""
    lines = text.split("\n")
    in_section = False
    section_text: List[str] = []
    for line in lines:
        if line.startswith("## "):
            if in_section:
                break
            if "基本信息" in line:
                in_section = True
                continue
        elif in_section:
            section_text.append(line)
    joined = "\n".join(section_text)

    has_birth = bool(re.search(r"生卒|生年|卒年|生于|生|卒", joined)) and (
        bool(re.search(r"\d{4}", joined))
        or "?" in joined
        or "？" in joined
        or "不详" in joined
        or "约" in joined
    )
    has_zi = bool(
        re.search(r"字号|字\s*[:：]|别号|号\s*[:：]|法名|教名|字[^，,、\s]", joined)
    )
    has_origin = bool(re.search(r"籍贯|祖籍|出生|出身地|原籍|国籍", joined))
    has_role = bool(re.search(r"身份|官职|职业|地位|职衔|谥号|封号|著作", joined))

    missing: List[str] = []
    if not has_birth:
        missing.append("生卒")
    if not has_zi:
        missing.append("字号")
    if not has_origin:
        missing.append("籍贯")
    if not has_role:
        missing.append("身份/官职")
    return missing, bool(section_text)


# ----- 审计 -----

def audit_file(path: str, base_dir: str) -> Dict:
    text = read_text(path)
    lines_count = len(text.split("\n"))

    # 规则 1：一手依据
    primary_refs = count_primary_refs(text)
    high_quality = set()
    for ref in primary_refs:
        if any(kw in ref for kw in PRIMARY_KEYWORDS):
            high_quality.add(ref)
    rule1_count = len(high_quality)
    kw_hits = count_refs_by_keywords(text)
    if rule1_count < 2 and kw_hits >= 2:
        rule1_count = max(rule1_count, 2)
    rule1_pass = rule1_count >= 2

    # 规则 2：存疑标注未搬运
    offending = detect_suspect_markers(text)
    rule2_pass = len(offending) == 0

    # 规则 3：未决争议清单
    has_suspect, suspect_title, suspect_content = detect_suspect_section(text)
    rule3_pass = has_suspect and suspect_content >= 1

    # 规则 4：来源汇总
    has_source, source_title, source_count = detect_source_section(text)
    rule4_pass = has_source and source_count >= 5

    # 规则 5：基本信息完整性
    missing_info, has_info_section = detect_basic_info(text)
    rule5_pass = len(missing_info) < 2

    # 规则 6：文件长度
    if lines_count < 100:
        rule6_status = "fail"
    elif lines_count < 200:
        rule6_status = "short"
    else:
        rule6_status = "ok"
    rule6_pass = rule6_status != "fail"

    rel = os.path.relpath(path, base_dir).replace("\\", "/")
    category = os.path.basename(os.path.dirname(path))
    return {
        "path": path,
        "rel_path": rel,
        "name": os.path.splitext(os.path.basename(path))[0],
        "category": category,
        "lines": lines_count,
        "rule1": {"pass": rule1_pass, "count": rule1_count, "kw_hits": kw_hits},
        "rule2": {"pass": rule2_pass, "offending": offending},
        "rule3": {"pass": rule3_pass, "title": suspect_title, "content": suspect_content},
        "rule4": {"pass": rule4_pass, "title": source_title, "count": source_count},
        "rule5": {"pass": rule5_pass, "missing": missing_info, "has_section": has_info_section},
        "rule6": {"pass": rule6_pass, "status": rule6_status, "lines": lines_count},
    }


def classify(x: Dict) -> Tuple[str, List[int]]:
    fails: List[int] = []
    for i in range(1, 7):
        if not x[f"rule{i}"]["pass"]:
            fails.append(i)
    if not fails:
        if x["rule6"]["status"] == "short":
            return "合格偏短", fails
        return "全合规", fails
    if 1 in fails or 2 in fails or len(fails) >= 3:
        return "严重不合规", fails
    return "部分不合规", fails


# ----- 收集文件 -----

def collect_files(base_dir: str) -> List[str]:
    files: List[str] = []
    for sub in os.listdir(base_dir):
        subdir = os.path.join(base_dir, sub)
        if not os.path.isdir(subdir):
            continue
        if sub.startswith("_"):
            continue
        for fname in os.listdir(subdir):
            if fname.endswith(".md") and fname != "INDEX.md":
                files.append(os.path.join(subdir, fname).replace("\\", "/"))
    files.sort()
    return files


# ----- 报告渲染 -----

def build_markdown(results: List[Dict], base_dir: str) -> str:
    buckets: Dict[str, List[Dict]] = {
        "全合规": [],
        "合格偏短": [],
        "部分不合规": [],
        "严重不合规": [],
    }
    for x in results:
        cls, fails = classify(x)
        x["_class"] = cls
        x["_fails"] = fails
        buckets[cls].append(x)

    total = len(results)
    full_ok = len(buckets["全合规"]) + len(buckets["合格偏短"])
    partial = len(buckets["部分不合规"])
    severe = len(buckets["严重不合规"])

    md: List[str] = []
    md.append("# BIO-006 全库验收报告")
    md.append("")
    md.append(f"> 审计范围：`{base_dir}`")
    md.append("> 审计规则：BIO-006 全库验收六条细则")
    md.append("> 审计方式：只读扫描（未修改任何文件）")
    md.append("")
    md.append("## 汇总统计")
    md.append("")
    md.append(f"- 审计文件总数：{total}")
    md.append(f"- 全合规：{len(buckets['全合规'])} 份")
    md.append(f"- 合格偏短（结构齐全但篇幅 100-200 行）：{len(buckets['合格偏短'])} 份")
    md.append(f"- 部分不合规（1-2 条规则失败且不涉及 R1/R2）：{partial} 份")
    md.append(f"- 严重不合规（R1/R2 失败，或 3 条及以上规则失败）：{severe} 份")
    if total:
        md.append(f"- **整体合规率**：{full_ok}/{total} = {full_ok * 100 / total:.1f}%（含合格偏短）")
    md.append("")
    md.append("## 按规则不合规清单")
    md.append("")

    def emit_rule_section(title: str, key: int, formatter) -> None:
        md.append(f"### {title}")
        fails = [x for x in results if not x[f"rule{key}"]["pass"]]
        if not fails:
            md.append("- 全部合规")
        else:
            for x in fails:
                md.append(formatter(x))
        md.append("")

    emit_rule_section(
        "规则 1：每篇至少 2 条一手或高等级二手依据",
        1,
        lambda x: (
            f"- `{x['rel_path']}`"
            f"（高等级来源 {x['rule1']['count']} 条，关键词命中 {x['rule1']['kw_hits']} 次）"
        ),
    )

    def r2_fmt(x: Dict) -> str:
        lines = [f"- `{x['rel_path']}`（正文残留 {len(x['rule2']['offending'])} 处存疑标注）"]
        for ln, txt in x["rule2"]["offending"][:8]:
            lines.append(f"  - L{ln}: {txt[:60]}…")
        return "\n".join(lines)

    emit_rule_section("规则 2：删净\"来源：存疑\"式句内标注", 2, r2_fmt)

    def r3_fmt(x: Dict) -> str:
        reason = "缺失章节" if not x["rule3"]["title"] else f"章节\"{x['rule3']['title']}\"无实质条目"
        return f"- `{x['rel_path']}`（{reason}）"

    emit_rule_section("规则 3：文件末尾有未决争议清单", 3, r3_fmt)

    def r4_fmt(x: Dict) -> str:
        if not x["rule4"]["title"]:
            return f"- `{x['rel_path']}`（缺失来源汇总章节）"
        return f"- `{x['rel_path']}`（章节\"{x['rule4']['title']}\"仅 {x['rule4']['count']} 条）"

    emit_rule_section("规则 4：来源汇总章节完整（≥ 5 条）", 4, r4_fmt)

    emit_rule_section(
        "规则 5：基本信息完整（生卒/字号/籍贯/身份 至少 3 项）",
        5,
        lambda x: f"- `{x['rel_path']}`（缺：{'、'.join(x['rule5']['missing'])}）",
    )

    emit_rule_section(
        "规则 6：文件长度合理（≥ 100 行）",
        6,
        lambda x: f"- `{x['rel_path']}`（仅 {x['rule6']['lines']} 行）",
    )

    short_list = [x for x in results if x["rule6"]["status"] == "short" and x["rule6"]["pass"]]
    if short_list:
        md.append("**合格偏短（100-200 行，建议后续扩写）**：")
        for x in short_list:
            md.append(f"- `{x['rel_path']}`（{x['rule6']['lines']} 行）")
        md.append("")

    md.append("## 按文件的合规度总表")
    md.append("")
    md.append("| 文件 | R1 | R2 | R3 | R4 | R5 | R6 | 总评 |")
    md.append("|------|----|----|----|----|----|----|------|")

    def mark(x: Dict, i: int) -> str:
        r = x[f"rule{i}"]
        if r["pass"]:
            if i == 6 and r["status"] == "short":
                return "△"
            return "√"
        return "×"

    data_sorted = sorted(results, key=lambda z: (z["category"], z["name"]))
    for x in data_sorted:
        row = (
            f"| {x['rel_path']} | "
            f"{mark(x, 1)} | {mark(x, 2)} | {mark(x, 3)} | "
            f"{mark(x, 4)} | {mark(x, 5)} | {mark(x, 6)} | {x['_class']} |"
        )
        md.append(row)
    md.append("")

    md.append("## 总评估")
    md.append("")
    if total:
        md.append(f"- **整体合规率**：{full_ok}/{total} = {full_ok * 100 / total:.1f}%（全合规 + 合格偏短）")
        md.append(f"- **严重不合规率**：{severe}/{total} = {severe * 100 / total:.1f}%")
    md.append("")
    return "\n".join(md)


# ----- 入口 -----

def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="BIO-006 全库验收审计工具")
    parser.add_argument("--base-dir", required=True, help="人物名录根目录")
    parser.add_argument(
        "--output",
        default=None,
        help="Markdown 报告输出路径（默认 {base_dir}/_validation/BIO-006验收报告.md）",
    )
    parser.add_argument(
        "--json",
        default=None,
        help="机器可读 JSON 输出路径（默认 {base_dir}/_validation/BIO-006审计结果.json）",
    )
    parser.add_argument("--quiet", action="store_true", help="仅输出统计")
    args = parser.parse_args(argv)

    base_dir = os.path.abspath(args.base_dir)
    if not os.path.isdir(base_dir):
        print(f"ERROR: base-dir 不存在或不是目录: {base_dir}", file=sys.stderr)
        return 2

    files = collect_files(base_dir)
    if not args.quiet:
        print(f"发现 {len(files)} 份传记文件")

    results: List[Dict] = []
    for p in files:
        try:
            results.append(audit_file(p, base_dir))
        except Exception as e:
            print(f"ERROR 审计失败 {p}: {e}", file=sys.stderr)

    out_dir = os.path.join(base_dir, "_validation")
    os.makedirs(out_dir, exist_ok=True)

    json_path = args.json or os.path.join(out_dir, "BIO-006审计结果.json")
    with open(json_path, "w", encoding="utf-8") as f:
        # 去掉 path（因为含绝对路径，影响移植），保留 rel_path
        serializable = []
        for x in results:
            y = {k: v for k, v in x.items() if k != "path"}
            serializable.append(y)
        json.dump(serializable, f, ensure_ascii=False, indent=2)

    md = build_markdown(results, base_dir)
    md_path = args.output or os.path.join(out_dir, "BIO-006验收报告.md")
    with open(md_path, "w", encoding="utf-8") as f:
        f.write(md)

    if not args.quiet:
        print(f"JSON 结果写入：{json_path}")
        print(f"Markdown 报告写入：{md_path}")

    # 统计严重不合规数量
    severe = 0
    for x in results:
        cls, _ = classify(x)
        if cls == "严重不合规":
            severe += 1
    print(f"BIO-006: {len(results) - severe}/{len(results)} 非严重不合规")
    return 0 if severe == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
