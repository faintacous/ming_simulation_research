#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
BIO-003 争议下沉扫描工具
========================

背景
----
BIO-003 要求：凡"存疑 / 待考 / 原始出处待查 / 野史 / 民间传说"等标记
必须统一迁入"存疑 & 待查"章节（或其别名），不得散落在基本信息、
关键事迹、正文结论等其他段落。

本脚本扫描传记 Markdown 中所有带上述关键字的行，并过滤掉：

1. 出现在"存疑 & 待查"章节内的行（属于合规位置）；
2. 章节标题行本身；
3. 明显的"元数据噪声"——例如："本档案综合…"、"编制说明"、
   "— 野史 —" 这种来源分级标签、"[2026-04-12 规范化]" 规范化注释等。

剩下的命中即"未下沉的争议字样"，输出到一份 Markdown 清单，供人工修订。

用法
----
    python bio003_dispute_scan.py --base-dir <人物名录根目录> \
        [--output <报告路径>] \
        [--exclude-file <忽略文件清单.txt>] \
        [--quiet]

参数说明
--------
- ``--base-dir``:     人物名录根目录。
- ``--output``:       Markdown 报告输出路径。默认
                      ``{base_dir}/_validation/BIO-003扫描报告.md``。
- ``--exclude-file``: 纯文本文件，每行一个要跳过的传记文件名（不含路径）。
                      用于"已修订文件"清单，避免重复扫描。
- ``--quiet``:        仅输出统计，不打印进度。

输入文件格式（``--exclude-file``）::

    张居正.md
    海瑞.md
    # 井号开头或空行会被忽略

退出码
------
- 0：没有遗留的争议字样；
- 1：存在未下沉的争议字样；
- 2：参数或文件系统错误。
"""

from __future__ import annotations

import argparse
import os
import re
import sys
from dataclasses import dataclass, field
from typing import List, Optional, Set, Tuple

# 要搜索的争议关键词
KEYWORDS = [
    "存疑",
    "待考",
    "原始出处待查",
    "来源：存疑",
    "待核",
    "野史",
    "民间传说",
]

# 合规的"存疑/争议"章节别名——出现在这些章节内的关键词不是违规
SUSPECT_SECTION_ALIASES = [
    "存疑 & 待查",
    "存疑&待查",
    "存疑与待查",
    "争议与待考",
    "争议与存疑",
    "未决争议",
    "待查",
    "存疑",
]

# 噪声模式：命中这些的行不算违规
NOISE_PATTERNS = [
    r"本档案",
    r"本报告",
    r"本文档",
    r"编制说明",
    r"编纂时间",
    r"— 野史 —",
    r"野史性质",
    r"民间传说与地方记忆",
    r"^\s*\[\d{4}-\d{2}-\d{2}\s*规范化\]",
    r"已移入",
    r"已迁入",
    r"\[原.*存疑",
    r"^\(续",
]


@dataclass
class Hit:
    """单条命中。"""

    line: int
    content: str
    section: str


@dataclass
class FileScan:
    """单文件扫描结果。"""

    rel_path: str
    hits: List[Hit] = field(default_factory=list)
    has_suspect_section: bool = False


def load_exclude_file(path: Optional[str]) -> Set[str]:
    """读取忽略文件清单。"""
    if not path:
        return set()
    result: Set[str] = set()
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            s = line.strip()
            if not s or s.startswith("#"):
                continue
            result.add(s)
    return result


def is_suspect_section_title(title: str) -> bool:
    """判断一个章节标题是否属于"存疑/争议"类。"""
    for alias in SUSPECT_SECTION_ALIASES:
        if alias in title:
            return True
    return False


def find_suspect_section_range(lines: List[str]) -> Tuple[Optional[int], int]:
    """返回存疑章节的 `[start, end)` 行号（从 0 开始的行索引）。"""
    start: Optional[int] = None
    end = len(lines)
    for i, line in enumerate(lines):
        m = re.match(r"^##\s+(.+?)\s*$", line)
        if m and is_suspect_section_title(m.group(1).strip()):
            start = i
            for j in range(i + 1, len(lines)):
                if re.match(r"^##\s", lines[j]) and not lines[j].startswith("### "):
                    end = j
                    break
            break
    return start, end


def scan_file(path: str, base_dir: str) -> FileScan:
    with open(path, "r", encoding="utf-8") as f:
        lines = f.readlines()

    rel = os.path.relpath(path, base_dir).replace("\\", "/")
    scan = FileScan(rel_path=rel)

    suspect_start, suspect_end = find_suspect_section_range(lines)
    scan.has_suspect_section = suspect_start is not None

    current_section = "(文件头)"
    for i, line in enumerate(lines):
        m = re.match(r"^(#{1,6})\s+(.*)", line)
        if m:
            level = len(m.group(1))
            if level <= 3:
                current_section = m.group(2).strip()
            continue  # 标题行本身不算命中
        # 在存疑区内不算违规
        if suspect_start is not None and suspect_start <= i < suspect_end:
            continue
        # 噪声过滤
        if any(re.search(p, line) for p in NOISE_PATTERNS):
            continue
        for kw in KEYWORDS:
            if kw in line:
                content = line.rstrip()
                if len(content) > 200:
                    content = content[:200] + "..."
                scan.hits.append(Hit(line=i + 1, content=content, section=current_section))
                break

    return scan


def collect_files(base_dir: str, exclude: Set[str]) -> List[str]:
    files: List[str] = []
    for dirpath, dirs, names in os.walk(base_dir):
        dirs[:] = [d for d in dirs if not d.startswith("_")]
        for name in names:
            if not name.endswith(".md"):
                continue
            if name == "INDEX.md":
                continue
            if name in exclude:
                continue
            files.append(os.path.join(dirpath, name))
    files.sort()
    return files


def build_report(scans: List[FileScan], base_dir: str) -> str:
    lines: List[str] = []
    total_files = len(scans)
    files_with_hits = [s for s in scans if s.hits]
    total_hits = sum(len(s.hits) for s in scans)

    lines.append("# BIO-003 争议下沉扫描报告")
    lines.append("")
    lines.append("> 审计规则：BIO-003（争议字样必须位于存疑 & 待查章节内）")
    lines.append(f"> 审计范围：`{base_dir}`")
    lines.append("")
    lines.append("## 汇总")
    lines.append("")
    lines.append(f"- 总扫描文件：{total_files}")
    lines.append(f"- 有命中文件：{len(files_with_hits)}")
    lines.append(f"- 总命中行数：{total_hits}")
    lines.append("")

    if not files_with_hits:
        lines.append("全部合规，无需修订。")
        return "\n".join(lines) + "\n"

    lines.append("## 逐文件清单")
    lines.append("")
    for s in files_with_hits:
        lines.append(f"### `{s.rel_path}`  (存疑节存在={s.has_suspect_section})")
        for h in s.hits:
            lines.append(f"- L{h.line} [{h.section}] {h.content}")
        lines.append("")

    return "\n".join(lines)


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="BIO-003 争议下沉扫描工具")
    parser.add_argument("--base-dir", required=True, help="人物名录根目录")
    parser.add_argument(
        "--output",
        default=None,
        help="Markdown 报告输出路径（默认 {base_dir}/_validation/BIO-003扫描报告.md）",
    )
    parser.add_argument(
        "--exclude-file",
        default=None,
        help="忽略文件清单（每行一个文件名，井号开头为注释）",
    )
    parser.add_argument("--quiet", action="store_true", help="仅输出统计")
    args = parser.parse_args(argv)

    base_dir = os.path.abspath(args.base_dir)
    if not os.path.isdir(base_dir):
        print(f"ERROR: base-dir 不存在或不是目录: {base_dir}", file=sys.stderr)
        return 2

    try:
        exclude = load_exclude_file(args.exclude_file)
    except OSError as e:
        print(f"ERROR: 读取 exclude-file 失败: {e}", file=sys.stderr)
        return 2

    files = collect_files(base_dir, exclude)
    if not args.quiet:
        print(f"发现 {len(files)} 份待扫描文件（已跳过 {len(exclude)} 份）")

    scans: List[FileScan] = []
    for p in files:
        try:
            scans.append(scan_file(p, base_dir))
        except Exception as e:
            print(f"ERROR 扫描失败 {p}: {e}", file=sys.stderr)

    md = build_report(scans, base_dir)
    out_path = args.output
    if not out_path:
        out_dir = os.path.join(base_dir, "_validation")
        os.makedirs(out_dir, exist_ok=True)
        out_path = os.path.join(out_dir, "BIO-003扫描报告.md")
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(md)
    if not args.quiet:
        print(f"Markdown 报告写入：{out_path}")

    total_hits = sum(len(s.hits) for s in scans)
    hit_files = sum(1 for s in scans if s.hits)
    print(f"BIO-003: {hit_files} 文件命中，共 {total_hits} 条违规")
    return 0 if total_hits == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
