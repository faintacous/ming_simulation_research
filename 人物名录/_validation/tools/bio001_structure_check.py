#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
BIO-001 结构重排检测工具
========================

背景
----
BIO-001 要求人物传记统一使用以下六段结构，顺序固定：

    基本信息 → 生平概要 → 关键事迹 → 争议与待考 → 来源汇总 → 关联主题

其中"争议与待考"允许以若干别名出现（"存疑 & 待查"、"未决争议"、
"争议与存疑"），"来源汇总"也有常见别名（"参考文献"、"主要来源"、
"史料来源"等）。其余章节（如"重要人际关系"、"历史评价"）不在 BIO-001
强制清单内，本脚本会保留它们但不参与顺序判断。

本脚本做什么
-----------
- 遍历 `{base_dir}/**/*.md`（排除 `INDEX.md` 和以 `_` 开头的目录）；
- 解析 Markdown 二级标题 `##`；
- 对每份传记检查：
  1. 是否齐备六个必备章节（以别名集合判断）；
  2. 必备章节的相对顺序是否与 BIO-001 一致；
  3. "基本信息"是否位于全文第一个二级章节。
- 生成一份 Markdown 报告，列出每份文件的缺失章节和顺序错误。

用法
----
    python bio001_structure_check.py --base-dir <人物名录根目录> \
        [--output <报告路径>] [--json <json路径>] [--quiet]

参数说明
--------
- ``--base-dir``: 人物名录根目录（包含"帝王/"、"阁臣重臣/"等子目录）。
- ``--output``:   Markdown 报告输出路径。默认
                  ``{base_dir}/_validation/BIO-001结构检查报告.md``。
- ``--json``:     机器可读 JSON 输出路径（可选）。
- ``--quiet``:    不打印进度，仅输出最终统计。

退出码
------
- 0：所有文件通过 BIO-001 结构检查；
- 1：存在不合规文件；
- 2：参数或文件系统错误。
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

# BIO-001 的章节清单，顺序固定。
# 每个元素是 (规范名, 别名列表, 是否必备)；别名包含规范名本身。
# 备注：关联主题在当前仓库内大量缺失，故列为可选；其余五段为必备。
REQUIRED_SECTIONS: List[Tuple[str, List[str], bool]] = [
    ("基本信息", ["基本信息"], True),
    ("生平概要", ["生平概要", "生平简介", "生平", "概述"], True),
    ("关键事迹", ["关键事迹", "主要事迹", "关键事实"], True),
    (
        "争议与待考",
        [
            "争议与待考",
            "争议与存疑",
            "存疑 & 待查",
            "存疑&待查",
            "存疑与待查",
            "未决争议",
            "待查",
            "存疑",
        ],
        True,
    ),
    (
        "来源汇总",
        [
            "来源汇总",
            "参考文献",
            "主要来源",
            "来源追踪",
            "主要参考",
            "史料来源",
            "资料来源",
            "引用来源",
            "参考资料",
        ],
        True,
    ),
    ("关联主题", ["关联主题", "相关主题", "关联条目", "相关条目"], False),
]


@dataclass
class FileReport:
    """单个文件的结构检查结果。"""

    path: str
    rel_path: str
    sections: List[Tuple[int, str]] = field(default_factory=list)  # [(line_no, title)]
    missing_required: List[str] = field(default_factory=list)  # 缺失的必备章节
    missing_optional: List[str] = field(default_factory=list)  # 缺失的可选章节（warning）
    order_errors: List[str] = field(default_factory=list)  # 顺序错误描述
    basic_info_first: bool = True  # 基本信息是否是第一个二级章节

    @property
    def passed(self) -> bool:
        """只有必备章节缺失、顺序错误、基本信息错位会导致不通过；可选章节缺失只是 warning。"""
        return not self.missing_required and not self.order_errors and self.basic_info_first

    @property
    def has_warning(self) -> bool:
        return bool(self.missing_optional)


def extract_sections(text: str) -> List[Tuple[int, str]]:
    """提取所有 `## ` 二级章节 `(line_no, title_text)`。"""
    result: List[Tuple[int, str]] = []
    for i, line in enumerate(text.splitlines(), start=1):
        m = re.match(r"^##\s+(.+?)\s*$", line)
        if not m:
            continue
        # 跳过三级及以上
        if line.startswith("### "):
            continue
        title = m.group(1).strip()
        result.append((i, title))
    return result


def match_section(title: str) -> Optional[str]:
    """把一个章节标题映射到 BIO-001 规范章节名；未命中返回 None。"""
    for canonical, aliases, _ in REQUIRED_SECTIONS:
        for alias in aliases:
            # 宽松匹配：别名是标题的子串即可，避免 "## 存疑 & 待查（按主题）" 这种情况失配
            if alias in title:
                return canonical
    return None


def check_file(path: str, base_dir: str) -> FileReport:
    """执行 BIO-001 结构检查。"""
    with open(path, "r", encoding="utf-8") as f:
        text = f.read()

    rel = os.path.relpath(path, base_dir).replace("\\", "/")
    report = FileReport(path=path, rel_path=rel)
    report.sections = extract_sections(text)

    # 把出现过的规范章节 → 首次出现位置
    seen: Dict[str, int] = {}
    for idx, (_, title) in enumerate(report.sections):
        canonical = match_section(title)
        if canonical and canonical not in seen:
            seen[canonical] = idx

    # 缺失章节（区分必备/可选）
    for canonical, _, required in REQUIRED_SECTIONS:
        if canonical not in seen:
            if required:
                report.missing_required.append(canonical)
            else:
                report.missing_optional.append(canonical)

    # 顺序检查：
    # - 主干四段（基本信息 → 生平概要 → 关键事迹 → 争议与待考）必须严格顺序；
    # - 尾部两段（来源汇总 / 关联主题）可任意顺序，但都必须排在主干四段之后；
    # 原因：历史模板有两种收尾次序，两种都接受。
    trunk = ["基本信息", "生平概要", "关键事迹", "争议与待考"]
    tail = ["来源汇总", "关联主题"]

    trunk_present = [c for c in trunk if c in seen]
    trunk_actual = sorted(trunk_present, key=lambda c: seen[c])
    if trunk_present != trunk_actual:
        report.order_errors.append(
            f"主干章节顺序为 {trunk_actual}，但 BIO-001 要求 {trunk_present}"
        )

    # 主干的最大位置必须小于尾部的最小位置
    if trunk_present:
        max_trunk_pos = max(seen[c] for c in trunk_present)
        for c in tail:
            if c in seen and seen[c] < max_trunk_pos:
                report.order_errors.append(
                    f"尾部章节 `{c}` 出现在主干章节之前"
                )

    # 基本信息是否为第一个 ## 章节
    if report.sections:
        first_title = report.sections[0][1]
        if match_section(first_title) != "基本信息":
            report.basic_info_first = False

    return report


def collect_files(base_dir: str) -> List[str]:
    """遍历 base_dir 下所有传记 .md 文件。"""
    files: List[str] = []
    for dirpath, dirs, names in os.walk(base_dir):
        # 跳过 _validation 和所有以 _ 开头的目录（备份/工作目录）
        dirs[:] = [d for d in dirs if not d.startswith("_")]
        for name in names:
            if not name.endswith(".md"):
                continue
            if name == "INDEX.md":
                continue
            files.append(os.path.join(dirpath, name))
    files.sort()
    return files


def build_markdown_report(reports: List[FileReport], base_dir: str) -> str:
    """把检查结果渲染为 Markdown 报告。"""
    total = len(reports)
    passed = sum(1 for r in reports if r.passed)
    warnings = sum(1 for r in reports if r.passed and r.has_warning)
    failed = total - passed

    lines: List[str] = []
    lines.append("# BIO-001 结构检查报告")
    lines.append("")
    lines.append(f"> 审计规则：BIO-001（全库结构重排）")
    lines.append(f"> 审计范围：`{base_dir}`")
    lines.append(
        "> 必备章节：基本信息 / 生平概要 / 关键事迹 / 争议与待考 / 来源汇总；"
        "可选章节：关联主题"
    )
    lines.append("")
    lines.append("## 汇总")
    lines.append("")
    lines.append(f"- 审计文件总数：{total}")
    lines.append(f"- 通过（含 warning）：{passed}")
    lines.append(f"  - 其中有 warning（仅缺可选章节）：{warnings}")
    lines.append(f"- 不通过：{failed}")
    if total:
        lines.append(f"- 合规率：{passed * 100 / total:.1f}%")
    lines.append("")

    lines.append("## 不通过清单")
    lines.append("")
    failing = [r for r in reports if not r.passed]
    if not failing:
        lines.append("- 全部通过")
        lines.append("")
    for r in failing:
        lines.append(f"### `{r.rel_path}`")
        if r.missing_required:
            lines.append(f"- 缺失必备章节：{', '.join(r.missing_required)}")
        if r.order_errors:
            for e in r.order_errors:
                lines.append(f"- 顺序错误：{e}")
        if not r.basic_info_first:
            first = r.sections[0][1] if r.sections else "(无)"
            lines.append(f"- 基本信息不是第一个二级章节（第一个是：`{first}`）")
        if r.missing_optional:
            lines.append(f"- [warning] 缺失可选章节：{', '.join(r.missing_optional)}")
        titles = " → ".join(t for _, t in r.sections) if r.sections else "(无章节)"
        lines.append(f"- 当前章节顺序：{titles}")
        lines.append("")

    # Warning 清单
    warning_list = [r for r in reports if r.passed and r.has_warning]
    if warning_list:
        lines.append("## Warning 清单（可选章节缺失，不影响通过）")
        lines.append("")
        for r in warning_list:
            lines.append(f"- `{r.rel_path}`：缺 {', '.join(r.missing_optional)}")
        lines.append("")

    lines.append("## 全部文件章节概览")
    lines.append("")
    lines.append("| 文件 | 结构 | 结论 |")
    lines.append("|------|------|------|")
    for r in reports:
        titles = " / ".join(t for _, t in r.sections) if r.sections else "(无章节)"
        if not r.passed:
            status = "×"
        elif r.has_warning:
            status = "△"
        else:
            status = "√"
        lines.append(f"| `{r.rel_path}` | {titles} | {status} |")
    lines.append("")

    return "\n".join(lines)


def reports_to_json(reports: List[FileReport]) -> List[dict]:
    """把检查结果转成可 JSON 序列化的列表。"""
    out = []
    for r in reports:
        out.append(
            {
                "rel_path": r.rel_path,
                "passed": r.passed,
                "has_warning": r.has_warning,
                "missing_required": r.missing_required,
                "missing_optional": r.missing_optional,
                "order_errors": r.order_errors,
                "basic_info_first": r.basic_info_first,
                "sections": [{"line": ln, "title": t} for ln, t in r.sections],
            }
        )
    return out


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="BIO-001 结构检查工具")
    parser.add_argument(
        "--base-dir",
        required=True,
        help="人物名录根目录（包含帝王/、阁臣重臣/ 等子目录）",
    )
    parser.add_argument(
        "--output",
        default=None,
        help="Markdown 报告输出路径（默认放在 {base_dir}/_validation/）",
    )
    parser.add_argument("--json", default=None, help="机器可读 JSON 输出路径（可选）")
    parser.add_argument("--quiet", action="store_true", help="仅输出统计，不打印进度")
    args = parser.parse_args(argv)

    base_dir = os.path.abspath(args.base_dir)
    if not os.path.isdir(base_dir):
        print(f"ERROR: base-dir 不存在或不是目录: {base_dir}", file=sys.stderr)
        return 2

    files = collect_files(base_dir)
    if not args.quiet:
        print(f"发现 {len(files)} 份传记文件")

    reports: List[FileReport] = []
    for p in files:
        try:
            r = check_file(p, base_dir)
            reports.append(r)
        except Exception as e:  # 单文件解析失败不阻断整体扫描
            print(f"ERROR 解析失败 {p}: {e}", file=sys.stderr)

    md = build_markdown_report(reports, base_dir)

    out_path = args.output
    if not out_path:
        out_dir = os.path.join(base_dir, "_validation")
        os.makedirs(out_dir, exist_ok=True)
        out_path = os.path.join(out_dir, "BIO-001结构检查报告.md")
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(md)
    if not args.quiet:
        print(f"Markdown 报告写入：{out_path}")

    if args.json:
        with open(args.json, "w", encoding="utf-8") as f:
            json.dump(reports_to_json(reports), f, ensure_ascii=False, indent=2)
        if not args.quiet:
            print(f"JSON 报告写入：{args.json}")

    passed = sum(1 for r in reports if r.passed)
    total = len(reports)
    print(f"BIO-001: {passed}/{total} 通过")
    return 0 if passed == total else 1


if __name__ == "__main__":
    sys.exit(main())
