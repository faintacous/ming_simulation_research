#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""BIO-009 字符级污染审计

扫描 人物名录/ 下的传记正文，定位编码失败遗留（U+FFFD）、
shell 命令残渣、异常字符与文件级异常。只读扫描，不修改任何传记。

产出：
  - 人物名录/_validation/BIO-009_污染审计_20260416.json
  - 人物名录/_validation/BIO-009_污染审计报告_20260416.md

使用：
  python tools/bio009_pollution_audit.py
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any


# ---------- 路径配置 ----------
ROOT = Path(__file__).resolve().parent.parent
BIO_DIR = ROOT / "人物名录"
OUT_DIR = BIO_DIR / "_validation"
JSON_OUT = OUT_DIR / "BIO-009_污染审计_20260416.json"
MD_OUT = OUT_DIR / "BIO-009_污染审计报告_20260416.md"

# 跳过目录（绝对匹配其路径中任一段）
SKIP_DIR_SEGMENTS = {"_validation"}
SKIP_DIR_PREFIXES = ("_backup_",)
# 跳过文件名
SKIP_FILES = {"INDEX.md"}

AUDIT_DATE = "2026-04-16"

# ---------- 检测规则 ----------

# A. 替换字符
REPLACEMENT_CHAR = "\ufffd"

# B. Shell 残渣
# 独占一行的 EOF/_EOF 标记（允许首尾空白）
RE_LONE_EOF = re.compile(r"^\s*_?EOF\s*$")
# 常见 shell 命令关键字（在正文中出现即可疑）
SHELL_KEYWORDS = [
    r"\bwc\s+-l\b",
    r"\bcat\s*<<-?\s*['\"]?\w*EOF\b",
    r"\bmkdir\s+-p\b",
    r"\brm\s+-rf?\b",
    r"\bgrep\s+-[a-zA-Z]+\b",
    r"\bsed\s+-i\b",
    r"\bawk\s+'",
    r"\$\([^)]{1,80}\)",   # 命令替换 $(...)
    r"\$\{[A-Za-z_][A-Za-z0-9_]*\}",  # 变量引用 ${...}
]
RE_SHELL_KEYWORDS = [re.compile(p) for p in SHELL_KEYWORDS]

# C. 异常字符
# 连续 ≥3 个相同中文句号 / 感叹号 / 问号
RE_REPEAT_PUNCT = re.compile(r"([。！？!?])\1{2,}")
# Unicode 私用区
RE_PUA = re.compile(r"[\uE000-\uF8FF]")
# 连续 ≥5 空行（实现见逐行累计）
MIN_CONSEC_BLANK = 5
# 尾部多余空白或不可见字符（除了 \n 本身）
RE_TRAILING_INVIS = re.compile(r"[ \t\u3000\xa0\u200b\ufeff]+\n?$")

# 代码块判定：三反引号围起来的段落视为代码区，内部命令不算残渣
FENCE_RE = re.compile(r"^\s*```")


def is_skip_path(p: Path) -> bool:
    """判断是否跳过该文件"""
    if p.name in SKIP_FILES:
        return True
    parts = p.parts
    for seg in parts:
        if seg in SKIP_DIR_SEGMENTS:
            return True
        for pref in SKIP_DIR_PREFIXES:
            if seg.startswith(pref):
                return True
    return False


def collect_files() -> list[Path]:
    """收集待审计 .md 文件"""
    files: list[Path] = []
    for p in BIO_DIR.rglob("*.md"):
        if is_skip_path(p):
            continue
        files.append(p)
    return sorted(files)


def make_context(line: str, col: int, radius: int = 20) -> str:
    """截取匹配处前后上下文"""
    start = max(0, col - radius)
    end = min(len(line), col + radius + 1)
    snippet = line[start:end].rstrip("\n")
    return snippet


def rel(p: Path) -> str:
    """相对仓库根的正斜杠路径"""
    return p.relative_to(ROOT).as_posix()


def audit_file(path: Path) -> dict[str, Any]:
    """对单个文件做四类审计，返回结构化结果"""
    # 读原始 bytes 以检测 BOM 与大小
    raw = path.read_bytes()
    size = len(raw)
    has_bom = raw.startswith(b"\xef\xbb\xbf")

    # 显式 UTF-8 解码
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        # 兜底：errors=replace 使管道不中断，但单独记录
        text = raw.decode("utf-8", errors="replace")

    lines = text.split("\n")
    last_line_empty = text.endswith("\n")
    # 文件末换行判断：若 text 不以 \n 结尾则缺尾换行
    missing_trailing_newline = (size > 0) and (not last_line_empty)

    replacement_char: list[dict[str, Any]] = []
    shell_residue: list[dict[str, Any]] = []
    anomaly_chars: list[dict[str, Any]] = []
    file_anomaly: list[dict[str, Any]] = []

    # 代码块状态（三反引号）
    in_fence = False
    blank_run = 0
    blank_run_start = 0
    # 章节上下文：位于「修订记录 / 校验日志 / 修订日志」等元信息章节时
    # 出现的 U+FFFD 往往是历史引用示例（如胡宗宪修订记录里保留原始错字样本），
    # 标记为 meta_section，由下游代理判断是否保留。
    # 使用 meta_section_level 记录进入 meta 章节时的标题层级（# 的数量）；
    # 遇到同级或更高级（# 数量 ≤ meta_section_level）的非 meta 标题时退出。
    meta_section_level = 0  # 0 表示当前不在 meta 区
    META_HEADER_RE = re.compile(
        r"^\s{0,3}(#{2,6})\s*(修订记录|校验日志|修订日志|审计记录|变更日志)"
    )
    NORMAL_HEADER_RE = re.compile(r"^\s{0,3}(#{1,6})\s+")

    for idx, line in enumerate(lines, start=1):
        # 代码围栏切换
        if FENCE_RE.match(line):
            in_fence = not in_fence

        # 元信息章节切换（层级感知）
        nh = NORMAL_HEADER_RE.match(line)
        if nh:
            level = len(nh.group(1))
            mh = META_HEADER_RE.match(line)
            if mh:
                meta_section_level = len(mh.group(1))
            elif meta_section_level > 0 and level <= meta_section_level:
                # 遇到同级或更高级的非 meta 标题，退出 meta 区
                meta_section_level = 0
        in_meta_section = meta_section_level > 0

        stripped_nl = line

        # A. 替换字符
        for m in re.finditer(REPLACEMENT_CHAR, stripped_nl):
            replacement_char.append(
                {
                    "file": rel(path),
                    "line": idx,
                    "col": m.start() + 1,
                    "context": make_context(stripped_nl, m.start()),
                    "in_meta_section": in_meta_section,
                }
            )

        # B. Shell 残渣（代码围栏内跳过）
        if not in_fence:
            if RE_LONE_EOF.match(stripped_nl):
                shell_residue.append(
                    {
                        "file": rel(path),
                        "line": idx,
                        "col": 1,
                        "pattern": "lone_EOF",
                        "context": stripped_nl.strip()[:80],
                    }
                )
            for pat in RE_SHELL_KEYWORDS:
                for m in pat.finditer(stripped_nl):
                    # 若匹配段落整体被反引号包裹（内联代码）则跳过
                    start = m.start()
                    end = m.end()
                    # 检查匹配段前后是否有未闭合的反引号对
                    before = stripped_nl[:start]
                    after = stripped_nl[end:]
                    if before.count("`") % 2 == 1 and after.count("`") >= 1:
                        # 位于内联代码中
                        continue
                    shell_residue.append(
                        {
                            "file": rel(path),
                            "line": idx,
                            "col": start + 1,
                            "pattern": pat.pattern,
                            "context": make_context(stripped_nl, start),
                        }
                    )

        # C. 异常字符
        for m in RE_REPEAT_PUNCT.finditer(stripped_nl):
            anomaly_chars.append(
                {
                    "file": rel(path),
                    "line": idx,
                    "col": m.start() + 1,
                    "type": "repeat_punct",
                    "match": m.group(0),
                    "context": make_context(stripped_nl, m.start()),
                }
            )
        for m in RE_PUA.finditer(stripped_nl):
            anomaly_chars.append(
                {
                    "file": rel(path),
                    "line": idx,
                    "col": m.start() + 1,
                    "type": "pua",
                    "match": repr(m.group(0)),
                    "context": make_context(stripped_nl, m.start()),
                }
            )
        if RE_TRAILING_INVIS.search(stripped_nl):
            anomaly_chars.append(
                {
                    "file": rel(path),
                    "line": idx,
                    "col": max(1, len(stripped_nl.rstrip()) + 1),
                    "type": "trailing_invisible",
                    "match": repr(stripped_nl[len(stripped_nl.rstrip()):]),
                    "context": stripped_nl.rstrip()[-40:],
                }
            )

        # 连续空行累计
        if stripped_nl.strip() == "":
            if blank_run == 0:
                blank_run_start = idx
            blank_run += 1
        else:
            if blank_run >= MIN_CONSEC_BLANK:
                anomaly_chars.append(
                    {
                        "file": rel(path),
                        "line": blank_run_start,
                        "col": 1,
                        "type": "consec_blank_lines",
                        "match": f"{blank_run} blank lines",
                        "context": f"L{blank_run_start}-L{idx - 1}",
                    }
                )
            blank_run = 0

    # 收尾连续空行
    if blank_run >= MIN_CONSEC_BLANK:
        anomaly_chars.append(
            {
                "file": rel(path),
                "line": blank_run_start,
                "col": 1,
                "type": "consec_blank_lines",
                "match": f"{blank_run} blank lines",
                "context": f"L{blank_run_start}-EOF",
            }
        )

    # D. 文件级异常
    if size == 0:
        file_anomaly.append({"file": rel(path), "type": "empty_file", "size": 0})
    elif size < 100:
        file_anomaly.append({"file": rel(path), "type": "too_small", "size": size})
    if has_bom:
        file_anomaly.append({"file": rel(path), "type": "utf8_bom", "size": size})
    if missing_trailing_newline:
        file_anomaly.append(
            {"file": rel(path), "type": "missing_trailing_newline", "size": size}
        )

    return {
        "file": rel(path),
        "size": size,
        "replacement_char": replacement_char,
        "shell_residue": shell_residue,
        "anomaly_chars": anomaly_chars,
        "file_anomaly": file_anomaly,
    }


def classify_b8(file_rel: str) -> bool:
    """判断文件是否属于 B 类 8 人修订对象"""
    b8_names = {"胡宗宪", "归有光", "徐渭", "董其昌", "王世贞", "李时珍", "汤显祖", "李贽"}
    stem = Path(file_rel).stem
    return stem in b8_names


def build_reports(per_file: list[dict[str, Any]]) -> None:
    findings = {
        "replacement_char": [],
        "shell_residue": [],
        "anomaly_chars": [],
        "file_anomaly": [],
    }
    polluted_set: set[str] = set()
    total_issues = 0

    for res in per_file:
        for k in findings:
            findings[k].extend(res[k])
            if res[k]:
                polluted_set.add(res["file"])
                total_issues += len(res[k])

    # 拆分 replacement_char 的真实污染 / 元信息区引用
    real_rc = [x for x in findings["replacement_char"] if not x.get("in_meta_section")]
    meta_rc = [x for x in findings["replacement_char"] if x.get("in_meta_section")]

    # 重算真实污染文件集合（排除只在元信息区出现的文件）
    real_polluted: set[str] = set()
    for res in per_file:
        has_real = (
            any(not x.get("in_meta_section") for x in res["replacement_char"])
            or bool(res["shell_residue"])
            or bool(res["anomaly_chars"])
            or bool(res["file_anomaly"])
        )
        if has_real:
            real_polluted.add(res["file"])

    report = {
        "audit_time": AUDIT_DATE,
        "total_scanned": len(per_file),
        "findings": findings,
        "summary": {
            "clean_files": len(per_file) - len(polluted_set),
            "polluted_files": len(polluted_set),
            "total_issues": total_issues,
            "real_polluted_files": len(real_polluted),
            "replacement_char_real": len(real_rc),
            "replacement_char_meta_ref": len(meta_rc),
        },
    }

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    JSON_OUT.write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    # ---------- Markdown 报告 ----------
    md: list[str] = []
    md.append(f"# BIO-009 字符级污染审计报告（{AUDIT_DATE}）\n")
    md.append("## 总览\n")
    md.append(f"- 扫描文件数：{report['total_scanned']}")
    md.append(f"- 洁净文件数：{report['summary']['clean_files']}")
    md.append(f"- 污染文件数：{report['summary']['polluted_files']}")
    md.append(f"- 污染点合计：{report['summary']['total_issues']}")
    md.append("")
    md.append("| 类别 | 命中数 |")
    md.append("|------|--------|")
    md.append(f"| A 替换字符 U+FFFD（正文） | {len(real_rc)} |")
    md.append(f"| A 替换字符 U+FFFD（修订记录引用） | {len(meta_rc)} |")
    md.append(f"| B Shell 残渣 | {len(findings['shell_residue'])} |")
    md.append(f"| C 异常字符 | {len(findings['anomaly_chars'])} |")
    md.append(f"| D 文件级异常 | {len(findings['file_anomaly'])} |")
    md.append("")
    md.append(f"**真正污染文件数（排除修订记录自引用）：{len(real_polluted)}**\n")

    # 按文件分组
    by_file: dict[str, dict[str, list]] = {}
    for cat in findings:
        for item in findings[cat]:
            f = item["file"]
            by_file.setdefault(f, {"replacement_char": [], "shell_residue": [],
                                   "anomaly_chars": [], "file_anomaly": []})
            by_file[f][cat].append(item)

    # B 类 8 人遗漏（判定：B 类 8 人且存在"正文级"污染——排除纯元信息区引用）
    def has_real_pollution(f: str) -> bool:
        cats = by_file[f]
        if any(not x.get("in_meta_section") for x in cats["replacement_char"]):
            return True
        return bool(cats["shell_residue"] or cats["anomaly_chars"] or cats["file_anomaly"])

    b8_missed = [f for f in sorted(by_file) if classify_b8(f) and has_real_pollution(f)]
    b8_meta_only = [f for f in sorted(by_file) if classify_b8(f) and not has_real_pollution(f)]

    md.append("## B 类 8 人修订遗漏\n")
    if not b8_missed:
        md.append("B 类 8 人（胡宗宪 / 归有光 / 徐渭 / 董其昌 / 王世贞 / 李时珍 / 汤显祖 / 李贽）"
                  "**正文均未检出字符级污染**，编辑代理修订闭环。\n")
    else:
        md.append("以下 B 类修订对象正文仍检出污染点，需补修：\n")
        for f in b8_missed:
            md.append(f"### {f}")
            cats = by_file[f]
            real_rc_f = [x for x in cats["replacement_char"] if not x.get("in_meta_section")]
            if real_rc_f:
                md.append(f"- **replacement_char（正文）** 命中 {len(real_rc_f)} 处")
                for it in real_rc_f[:10]:
                    md.append(f"  - L{it['line']}:{it['col']} `{it['context']}`")
            for cat in ("shell_residue", "anomaly_chars", "file_anomaly"):
                items = cats[cat]
                if not items:
                    continue
                md.append(f"- **{cat}** 命中 {len(items)} 处")
                for it in items[:10]:
                    md.append(f"  - L{it.get('line', '-')}:{it.get('col', '-')} "
                              f"`{it.get('context', it.get('type', ''))}`")
            md.append("")

    if b8_meta_only:
        md.append("### 备注：以下 B 类 8 人文件仅在「修订记录」章节残留历史引用示例，"
                  "正文已洁净，无需再修：\n")
        for f in b8_meta_only:
            meta_rc_f = [x for x in by_file[f]["replacement_char"]
                         if x.get("in_meta_section")]
            md.append(f"- {Path(f).stem}（`{f}`）：修订记录区保留 "
                      f"{len(meta_rc_f)} 处 U+FFFD 作为错误示例")
        md.append("")

    # 全库详情
    md.append("## 全库污染详情（按人物）\n")
    if not by_file:
        md.append("全库无污染命中，审计通过。\n")
    else:
        for f in sorted(by_file):
            stem = Path(f).stem
            md.append(f"### {stem}（`{f}`）")
            cats = by_file[f]

            if cats["replacement_char"]:
                md.append(f"**A. 替换字符 U+FFFD — {len(cats['replacement_char'])} 处**")
                for it in cats["replacement_char"]:
                    tag = "【修订记录引用·可忽略】" if it.get("in_meta_section") else "【正文污染·需修】"
                    md.append(f"- {tag} L{it['line']}:{it['col']} 上下文 `{it['context']}`")
                    if not it.get("in_meta_section"):
                        md.append(f"  - 修订指令：将 L{it['line']} 第 {it['col']} 位的 `U+FFFD` "
                                  f"替换为正确汉字（需人工根据上下文推断）")
            if cats["shell_residue"]:
                md.append(f"**B. Shell 残渣 — {len(cats['shell_residue'])} 处**")
                for it in cats["shell_residue"]:
                    md.append(f"- L{it['line']}:{it['col']} 模式 `{it['pattern']}` "
                              f"上下文 `{it['context']}`")
                    md.append(f"  - 修订指令：删除 L{it['line']} 的 shell 残渣行 / 片段")
            if cats["anomaly_chars"]:
                md.append(f"**C. 异常字符 — {len(cats['anomaly_chars'])} 处**")
                for it in cats["anomaly_chars"]:
                    md.append(f"- L{it['line']}:{it['col']} 类型 `{it['type']}` "
                              f"匹配 `{it.get('match', '')}` 上下文 `{it.get('context', '')}`")
            if cats["file_anomaly"]:
                md.append(f"**D. 文件级异常 — {len(cats['file_anomaly'])} 处**")
                for it in cats["file_anomaly"]:
                    md.append(f"- 类型 `{it['type']}` size={it.get('size', '-')}")
                    if it["type"] == "utf8_bom":
                        md.append(f"  - 修订指令：移除文件开头 BOM（EF BB BF）")
                    elif it["type"] == "missing_trailing_newline":
                        md.append(f"  - 修订指令：在文件末追加一个换行符")
                    elif it["type"] == "empty_file":
                        md.append(f"  - 修订指令：文件为空，需重建或删除占位")
                    elif it["type"] == "too_small":
                        md.append(f"  - 修订指令：文件过小（<100 字节），检查是否空壳")
            md.append("")

    md.append("## 说明\n")
    md.append("- 本脚本仅做只读审计，不修改传记正文。")
    md.append("- 实际清理由后续独立编辑代理按本报告的「修订指令」执行。")
    md.append("- 跳过目录：`_validation/`、`_backup_*/`；跳过文件：`INDEX.md`。")
    md.append("- 代码围栏（三反引号）与内联反引号内的 shell 关键字不计入残渣。")
    md.append("")

    MD_OUT.write_text("\n".join(md), encoding="utf-8")


def main() -> None:
    files = collect_files()
    per_file = [audit_file(p) for p in files]
    build_reports(per_file)
    # 控制台简报
    total_issues = sum(
        len(r["replacement_char"]) + len(r["shell_residue"])
        + len(r["anomaly_chars"]) + len(r["file_anomaly"])
        for r in per_file
    )
    polluted = sum(
        1 for r in per_file
        if r["replacement_char"] or r["shell_residue"]
        or r["anomaly_chars"] or r["file_anomaly"]
    )
    print(f"[BIO-009] scanned={len(per_file)} polluted={polluted} "
          f"total_issues={total_issues}")
    print(f"[BIO-009] JSON → {rel(JSON_OUT)}")
    print(f"[BIO-009] MD   → {rel(MD_OUT)}")


if __name__ == "__main__":
    main()
