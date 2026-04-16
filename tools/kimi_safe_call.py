#!/usr/bin/env python3
"""
kimi_safe_call.py — Kimi CLI 安全包装脚本
==========================================

用途：
  规避 Windows GBK 控制台崩溃、稳定捕获 429 配额耗尽、
  强制 Kimi 自写 .md 到指定路径而非 stdout pipe。

用法：
  # 正常调用：把 prompt 存进文件再传进来，Kimi 产物写到 --out-file
  python tools/kimi_safe_call.py --prompt-file tmp/my_prompt.txt --out-file tmp/kimi_result.md
  python tools/kimi_safe_call.py --prompt "人物传记研究 xxx" --out-file tmp/xxx.md --topic xxx

  # Ping 探测：铺量前先打 1 次，判断是否 429
  python tools/kimi_safe_call.py --ping

退出码：
  0   成功
  429 配额耗尽（rate_limit_reached_error 或 429）
  124 超时
  1   其他错误

约束：
  - 必须在 Windows 下也能稳定运行（已设 PYTHONUTF8 / PYTHONIOENCODING）
  - 禁用 stdout pipe 渲染依赖：Kimi 自写文件到 work_dir，本脚本再搬到 --out-file
  - 不复用训练记忆的默认路径；所有路径用正斜杠/绝对路径
"""
from __future__ import annotations

import argparse
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

# ── 配置 ────────────────────────────────────────────
KIMI_BIN = os.environ.get("KIMI_BIN", "C:/Users/pc/.local/bin/kimi")
DEFAULT_TIMEOUT = int(os.environ.get("KIMI_TIMEOUT", "1800"))
DEFAULT_MAX_STEPS = os.environ.get("KIMI_MAX_STEPS", "30")

EXIT_OK = 0
EXIT_QUOTA = 429
EXIT_TIMEOUT = 124
EXIT_OTHER = 1

QUOTA_PATTERNS = [
    r"rate_limit_reached",
    r"\b429\b",
    r"quota.*exceed",
    r"too many requests",
]


def _build_env() -> dict:
    env = os.environ.copy()
    env["PYTHONUTF8"] = "1"
    env["PYTHONIOENCODING"] = "utf-8"
    return env


def _looks_like_quota_error(text: str) -> bool:
    if not text:
        return False
    lowered = text.lower()
    return any(re.search(p, lowered) for p in QUOTA_PATTERNS)


def _run_kimi(prompt: str, timeout: int) -> subprocess.CompletedProcess | None:
    """在临时 cwd 中执行 kimi，返回 CompletedProcess；超时返回 None。"""
    work_dir = tempfile.mkdtemp(prefix="kimi_safe_")
    try:
        cp = subprocess.run(
            [
                KIMI_BIN,
                "--print",
                "--max-steps-per-turn", DEFAULT_MAX_STEPS,
                "-p", prompt,
            ],
            cwd=work_dir,
            capture_output=True,
            text=True,
            env=_build_env(),
            timeout=timeout,
            encoding="utf-8",
            errors="replace",
        )
        # 把 work_dir 挂在属性上供调用方搬运
        cp.work_dir = work_dir  # type: ignore[attr-defined]
        return cp
    except subprocess.TimeoutExpired:
        return None


def do_ping() -> int:
    """轻量探测：发一个最小 prompt，判断是否 429。"""
    prompt = "回答一个字：好"
    t0 = time.time()
    cp = _run_kimi(prompt, timeout=60)
    elapsed = time.time() - t0

    if cp is None:
        print(f"[kimi-ping] TIMEOUT after 60s (elapsed={elapsed:.0f}s)", file=sys.stderr)
        return EXIT_TIMEOUT

    stdout = cp.stdout or ""
    stderr = cp.stderr or ""

    if _looks_like_quota_error(stdout) or _looks_like_quota_error(stderr):
        print(f"[kimi-ping] QUOTA (429) detected (elapsed={elapsed:.0f}s)", file=sys.stderr)
        print(f"[kimi-ping] stderr tail: {stderr[-300:]}", file=sys.stderr)
        return EXIT_QUOTA

    if cp.returncode != 0:
        print(f"[kimi-ping] FAIL rc={cp.returncode} (elapsed={elapsed:.0f}s)", file=sys.stderr)
        print(f"[kimi-ping] stderr tail: {stderr[-300:]}", file=sys.stderr)
        return EXIT_OTHER

    print(f"[kimi-ping] OK (elapsed={elapsed:.0f}s)")
    return EXIT_OK


def do_call(prompt: str, out_file: Path, topic: str, timeout: int) -> int:
    out_file = out_file.resolve()
    out_file.parent.mkdir(parents=True, exist_ok=True)

    # 在 prompt 里指示 Kimi 自写文件（避免 stdout pipe 渲染崩溃）
    target_name = f"{topic}.md"
    augmented = (
        prompt
        + f"\n\n请把研究成果完整写入当前工作目录的 {target_name} 文件（UTF-8 编码）。"
        + "不要只在 stdout 输出正文，务必落盘为文件。"
    )

    print(f"[kimi-safe] start topic={topic} out={out_file} timeout={timeout}s")
    t0 = time.time()
    cp = _run_kimi(augmented, timeout=timeout)
    elapsed = time.time() - t0

    if cp is None:
        print(f"[kimi-safe] TIMEOUT after {timeout}s", file=sys.stderr)
        return EXIT_TIMEOUT

    stdout = cp.stdout or ""
    stderr = cp.stderr or ""
    work_dir = Path(getattr(cp, "work_dir", ""))  # type: ignore[arg-type]

    if _looks_like_quota_error(stdout) or _looks_like_quota_error(stderr):
        print(f"[kimi-safe] QUOTA (429) (elapsed={elapsed:.0f}s)", file=sys.stderr)
        print(f"[kimi-safe] stderr tail: {stderr[-400:]}", file=sys.stderr)
        return EXIT_QUOTA

    # 搬运产物
    candidate = work_dir / target_name
    if not candidate.exists():
        md_files = list(work_dir.glob("*.md"))
        if md_files:
            candidate = md_files[0]

    if candidate.exists():
        shutil.copyfile(candidate, out_file)
        size = out_file.stat().st_size
        lines = len(out_file.read_text(encoding="utf-8", errors="replace").splitlines())
        print(f"[kimi-safe] OK lines={lines} bytes={size} elapsed={elapsed:.0f}s")
        return EXIT_OK

    print(
        f"[kimi-safe] FAIL no output file in work_dir={work_dir} rc={cp.returncode} "
        f"elapsed={elapsed:.0f}s",
        file=sys.stderr,
    )
    print(f"[kimi-safe] stderr tail: {stderr[-400:]}", file=sys.stderr)
    print(f"[kimi-safe] stdout tail: {stdout[-400:]}", file=sys.stderr)
    return EXIT_OTHER


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(
        description="Kimi CLI 安全包装（UTF-8 环境、自写文件、429 标准化退出码）",
    )
    parser.add_argument("--ping", action="store_true", help="轻量探测：判断是否 429")
    parser.add_argument("--prompt", type=str, default=None, help="内联 prompt 字符串")
    parser.add_argument("--prompt-file", type=Path, default=None, help="prompt 文件路径（UTF-8）")
    parser.add_argument("--out-file", type=Path, default=None, help="Kimi 产物落盘目标路径（.md）")
    parser.add_argument("--topic", type=str, default="kimi_output", help="工作目录文件名前缀")
    parser.add_argument("--timeout", type=int, default=DEFAULT_TIMEOUT, help="秒，默认 1800")
    args = parser.parse_args(argv)

    if args.ping:
        return do_ping()

    if not args.out_file:
        parser.error("--out-file 必填（除非使用 --ping）")

    if args.prompt and args.prompt_file:
        parser.error("--prompt 与 --prompt-file 二选一")
    if not args.prompt and not args.prompt_file:
        parser.error("必须提供 --prompt 或 --prompt-file")

    if args.prompt_file:
        prompt_text = args.prompt_file.read_text(encoding="utf-8")
    else:
        prompt_text = args.prompt  # type: ignore[assignment]

    return do_call(prompt_text, args.out_file, args.topic, args.timeout)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
