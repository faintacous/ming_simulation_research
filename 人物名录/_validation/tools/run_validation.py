#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
人物传记验收流水线（主入口）
============================

这是一个总调度器，把 ``tools/`` 目录下各个单点检查工具串成一条
可单独运行、也可一次跑全的流水线：

- ``--bio001``：BIO-001 结构检查（`bio001_structure_check.py`）
- ``--bio003``：BIO-003 争议下沉扫描（`bio003_dispute_scan.py`）
- ``--bio006``：BIO-006 全库验收审计（`bio006_audit.py`）
- ``--all``：上述三项全跑

备注：BIO-002（来源分级）和 BIO-004/005（时间称谓、交叉一致性）目前
没有稳定的机器检测规则，仍须依赖人工校验报告和跨条目比对；本脚本为它们
预留了占位项，后续补实现时直接在 ``RULES`` 注册表里加钩子即可。

用法
----
    python tools/run_validation.py --all
    python tools/run_validation.py --bio003
    python tools/run_validation.py --bio006 --output report.md

常用示例
--------
默认工作目录在人物名录根目录时::

    # 全量跑，报告落在 _validation/ 下
    python _validation/tools/run_validation.py \
        --base-dir . --all

    # 只跑 BIO-003，指定输出文件
    python _validation/tools/run_validation.py \
        --base-dir . --bio003 --output _validation/BIO-003扫描报告.md

    # 跑全部并把各 JSON 结果落到同一目录
    python _validation/tools/run_validation.py \
        --base-dir . --all --json-dir _validation/json

退出码
------
- 0：所有启用的规则均通过；
- 1：任一规则不通过；
- 2：参数或文件系统错误。
"""

from __future__ import annotations

import argparse
import os
import sys
from typing import List, Optional

# 把同目录加入 sys.path，便于作为独立脚本直接运行 import
_THIS_DIR = os.path.dirname(os.path.abspath(__file__))
if _THIS_DIR not in sys.path:
    sys.path.insert(0, _THIS_DIR)

import bio001_structure_check  # noqa: E402
import bio003_dispute_scan  # noqa: E402
import bio006_audit  # noqa: E402


def resolve_out(base_dir: str, filename: str, override: Optional[str]) -> str:
    if override:
        return override
    out_dir = os.path.join(base_dir, "_validation")
    os.makedirs(out_dir, exist_ok=True)
    return os.path.join(out_dir, filename)


def run_bio001(base_dir: str, output: Optional[str], json_dir: Optional[str], quiet: bool) -> int:
    args = ["--base-dir", base_dir]
    args += ["--output", resolve_out(base_dir, "BIO-001结构检查报告.md", output)]
    if json_dir:
        os.makedirs(json_dir, exist_ok=True)
        args += ["--json", os.path.join(json_dir, "BIO-001结构检查.json")]
    if quiet:
        args.append("--quiet")
    print("--- 运行 BIO-001 ---")
    return bio001_structure_check.main(args)


def run_bio003(
    base_dir: str,
    output: Optional[str],
    exclude_file: Optional[str],
    quiet: bool,
) -> int:
    args = ["--base-dir", base_dir]
    args += ["--output", resolve_out(base_dir, "BIO-003扫描报告.md", output)]
    if exclude_file:
        args += ["--exclude-file", exclude_file]
    if quiet:
        args.append("--quiet")
    print("--- 运行 BIO-003 ---")
    return bio003_dispute_scan.main(args)


def run_bio006(base_dir: str, output: Optional[str], json_dir: Optional[str], quiet: bool) -> int:
    args = ["--base-dir", base_dir]
    args += ["--output", resolve_out(base_dir, "BIO-006验收报告.md", output)]
    if json_dir:
        os.makedirs(json_dir, exist_ok=True)
        args += ["--json", os.path.join(json_dir, "BIO-006审计结果.json")]
    if quiet:
        args.append("--quiet")
    print("--- 运行 BIO-006 ---")
    return bio006_audit.main(args)


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description="人物传记验收流水线（BIO-001 / BIO-003 / BIO-006）"
    )
    parser.add_argument(
        "--base-dir",
        required=True,
        help="人物名录根目录（例如 `人物名录/`）",
    )
    parser.add_argument("--all", action="store_true", help="运行全部规则（BIO-001 + BIO-003 + BIO-006）")
    parser.add_argument("--bio001", action="store_true", help="运行 BIO-001 结构检查")
    parser.add_argument("--bio003", action="store_true", help="运行 BIO-003 争议下沉扫描")
    parser.add_argument("--bio006", action="store_true", help="运行 BIO-006 全库验收审计")
    parser.add_argument(
        "--output",
        default=None,
        help="Markdown 报告输出路径（仅对单规则生效；--all 模式下使用各自默认路径）",
    )
    parser.add_argument(
        "--json-dir",
        default=None,
        help="把 BIO-001 / BIO-006 的 JSON 结果写入该目录",
    )
    parser.add_argument(
        "--exclude-file",
        default=None,
        help="BIO-003 的已修订文件清单（每行一个文件名，井号开头为注释）",
    )
    parser.add_argument("--quiet", action="store_true", help="仅输出统计，不打印进度")
    args = parser.parse_args(argv)

    base_dir = os.path.abspath(args.base_dir)
    if not os.path.isdir(base_dir):
        print(f"ERROR: base-dir 不存在或不是目录: {base_dir}", file=sys.stderr)
        return 2

    # 如果没有指定任何规则也没指定 --all，视同 --all
    if not (args.all or args.bio001 or args.bio003 or args.bio006):
        args.all = True

    # 在 --all 模式下不允许 --output（因为要给多个规则分别落盘）
    if args.all and args.output:
        print(
            "ERROR: --all 与 --output 互斥；--all 模式请使用 --json-dir 或各规则的默认路径",
            file=sys.stderr,
        )
        return 2

    codes: List[int] = []
    if args.all or args.bio001:
        codes.append(run_bio001(base_dir, args.output if args.bio001 and not args.all else None,
                                args.json_dir, args.quiet))
    if args.all or args.bio003:
        codes.append(
            run_bio003(
                base_dir,
                args.output if args.bio003 and not args.all else None,
                args.exclude_file,
                args.quiet,
            )
        )
    if args.all or args.bio006:
        codes.append(run_bio006(base_dir, args.output if args.bio006 and not args.all else None,
                                args.json_dir, args.quiet))

    print("")
    print("=== 总结 ===")
    failed = [c for c in codes if c != 0]
    if failed:
        print(f"有 {len(failed)}/{len(codes)} 项规则未通过，请查看上方报告。")
        return 1
    print(f"全部 {len(codes)} 项规则通过。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
