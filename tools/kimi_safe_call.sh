#!/usr/bin/env bash
# kimi_safe_call.sh — Kimi CLI 安全包装（bash 版，Windows/Git Bash 可用）
# =====================================================================
#
# 用途：
#   规避 Windows GBK 控制台崩溃、稳定捕获 429 配额耗尽、
#   强制 Kimi 自写 .md 到 --out-file，不走 stdout pipe。
#
# 用法：
#   # 正常调用
#   tools/kimi_safe_call.sh --prompt-file tmp/p.txt --out-file tmp/r.md --topic xxx
#   tools/kimi_safe_call.sh --prompt "研究 xxx" --out-file tmp/r.md --topic xxx
#
#   # Ping 探测：铺量前先打 1 次，判断是否 429
#   tools/kimi_safe_call.sh --ping
#
# 退出码：0=成功 / 429=配额 / 124=超时 / 1=其他错
#
# 实现说明：
#   本脚本仅做 CLI 参数转发，实际逻辑由同目录 kimi_safe_call.py 完成，
#   避免 bash 与 python 双重维护。

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PY_SCRIPT="${SCRIPT_DIR}/kimi_safe_call.py"

if [[ ! -f "${PY_SCRIPT}" ]]; then
    echo "[kimi-safe-sh] missing ${PY_SCRIPT}" >&2
    exit 1
fi

# 强制 UTF-8（即便 Git Bash 默认 locale 异常也覆盖）
export PYTHONUTF8=1
export PYTHONIOENCODING=utf-8
export LC_ALL="${LC_ALL:-C.UTF-8}"

PY_BIN="${PYTHON_BIN:-python}"
if ! command -v "${PY_BIN}" >/dev/null 2>&1; then
    PY_BIN="python3"
fi

exec "${PY_BIN}" "${PY_SCRIPT}" "$@"
