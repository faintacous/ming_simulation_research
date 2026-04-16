#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
BIO-008 头部标签审计脚本

扫描 人物名录/**/*.md（跳过 _validation、_backup_*、INDEX.md 等非传记文件），
对每份传记统计实际一手引用数/百科占比/修订记录条数/行数，
对比头部 `> **来源质量评估**：...` 标签，判定是否过期。

输出：
- 人物名录/_validation/BIO-008_标签审计_20260416.json
- 人物名录/_validation/BIO-008_标签审计报告_20260416.md

脚本只读 + 产出报告，不修改传记正文。
"""

import json
import re
from pathlib import Path
from datetime import datetime

# 项目根目录
ROOT = Path(__file__).resolve().parent.parent
BIO_DIR = ROOT / "人物名录"
VALID_DIR = BIO_DIR / "_validation"
AUDIT_DATE = "2026-04-16"

OUT_JSON = VALID_DIR / f"BIO-008_标签审计_{AUDIT_DATE.replace('-', '')}.json"
OUT_MD = VALID_DIR / f"BIO-008_标签审计报告_{AUDIT_DATE.replace('-', '')}.md"

# --- 跳过规则 ---
# 顶层目录黑名单（相对 BIO_DIR）
SKIP_DIRS = {"_validation", "_reports", "tools"}
SKIP_DIR_PREFIX = "_backup_"  # 任意 _backup_* 目录
SKIP_FILES = {"INDEX.md"}


def should_skip(path: Path) -> bool:
    """判断文件是否应跳过"""
    rel = path.relative_to(BIO_DIR)
    parts = rel.parts
    # 根目录下的白名单文件
    if path.name in SKIP_FILES:
        return True
    # 顶层目录过滤
    if parts and parts[0] in SKIP_DIRS:
        return True
    if parts and parts[0].startswith(SKIP_DIR_PREFIX):
        return True
    return False


# --- 统计规则 ---

# 一手古籍名（带卷次形式的识别；覆盖简写"明史卷222"和正式"《明史·XXX传》卷 222"）
# 思路：凡出现以下关键词之一 + 紧邻卷号/本传标识，都算一次一手引用
PRIMARY_PATTERNS = [
    # 简写：明史卷222 / 明实录卷XXX
    re.compile(r"明史卷\s*\d+"),
    re.compile(r"明实录卷\s*\d+"),
    re.compile(r"明[世穆神熹宣英宪孝武光熹思]宗实录卷\s*\d+"),
    re.compile(r"大明会典卷\s*\d+"),
    re.compile(r"明史纪事本末卷\s*\d+"),
    # 书名号形式
    re.compile(r"《明史[·・][^》]{0,40}》(?:\s*卷\s*\d+)?"),
    re.compile(r"《明史》\s*卷\s*\d+"),
    re.compile(r"《明[世穆神熹宣英宪孝武光思]宗实录》(?:\s*卷\s*\d+)?"),
    re.compile(r"《明实录》(?:\s*卷\s*\d+)?"),
    re.compile(r"《大明会典》(?:\s*卷\s*\d+)?"),
    re.compile(r"《明史纪事本末》(?:\s*卷\s*\d+)?"),
    re.compile(r"《大明律》(?:\s*卷\s*\d+)?"),
    re.compile(r"《皇明经世文编》(?:\s*卷\s*\d+)?"),
    re.compile(r"《满文老档》"),
    re.compile(r"《清太祖高皇帝实录》(?:\s*卷\s*\d+)?"),
    re.compile(r"《朝鲜王朝实录[·・]?[^》]*》"),
]

# 用于去重卷号提取：匹配"XX卷 N"或"《XX》卷 N"里的数字卷号
VOLUME_EXTRACT = [
    (re.compile(r"明史卷\s*(\d+)"), "明史"),
    (re.compile(r"《明史[·・][^》]{0,40}》\s*卷\s*(\d+)"), "明史"),
    (re.compile(r"《明史》\s*卷\s*(\d+)"), "明史"),
    (re.compile(r"明实录卷\s*(\d+)"), "明实录"),
    (re.compile(r"《明实录》\s*卷\s*(\d+)"), "明实录"),
    (re.compile(r"《明(世|穆|神|熹|宣|英|宪|孝|武|光|思)宗实录》\s*卷\s*(\d+)"), "实录"),
    (re.compile(r"明(世|穆|神|熹|宣|英|宪|孝|武|光|思)宗实录卷\s*(\d+)"), "实录"),
    (re.compile(r"《大明会典》\s*卷\s*(\d+)"), "会典"),
    (re.compile(r"大明会典卷\s*(\d+)"), "会典"),
    (re.compile(r"《明史纪事本末》\s*卷\s*(\d+)"), "纪事本末"),
    (re.compile(r"《大明律》\s*卷\s*(\d+)"), "大明律"),
    (re.compile(r"《皇明经世文编》\s*卷\s*(\d+)"), "经世文编"),
    (re.compile(r"《清太祖高皇帝实录》\s*卷\s*(\d+)"), "清实录"),
]

# 百科类来源（含百度/维基/搜狗等常见二手百科）
ENCYCLOPEDIA_PATTERN = re.compile(r"百度百科|维基百科|搜狗百科|360百科|互动百科|wikipedia|Wikipedia|WIKIPEDIA")

# 头部标签（匹配"> **来源质量评估**：XX"整行，兼容老/新格式）
HEADER_LABEL_PATTERN = re.compile(
    r"^>\s*\*\*来源质量评估\*\*[：:]\s*(?P<body>.+)$", re.MULTILINE
)

# 标签里尝试抽"一手数"、"百科占比"
LABEL_PRIMARY_PATTERN = re.compile(r"一手[^:：]*[:：]\s*(\d+)")
LABEL_ENCYCLO_PCT_PATTERN = re.compile(r"(?:百科[^:：]*[:：]\s*\d+\s*条[^（]*[（(]?占?\s*(\d+)\s*%|百科占比[:：]\s*(\d+)\s*%|占\s*(\d+)\s*%)")

# 修订记录识别：`## 修订记录` 小节下的 `### ` 子小节数
REVISION_HEADER = "## 修订记录"


def classify_risk(primary: int, enc_ratio: float) -> str:
    """按 BIO-002 分级标准判定风险等级"""
    if primary <= 2 or enc_ratio > 0.5:
        return "高风险"
    if primary >= 10 and enc_ratio < 0.3:
        return "低风险"
    return "中风险"


def count_primary(text: str) -> int:
    """统计一手古籍引用出现次数（每次匹配算一次）"""
    total = 0
    for pat in PRIMARY_PATTERNS:
        total += len(pat.findall(text))
    return total


def count_unique_volumes(text: str) -> int:
    """去重统计一手卷号：同一"书名+卷号"只算一次"""
    seen = set()
    for pat, book in VOLUME_EXTRACT:
        for m in pat.finditer(text):
            # 有的正则是两组（朝代+数字），取最后一组作为卷号
            vol = m.group(m.lastindex)
            seen.add((book, vol))
    return len(seen)


def count_encyclopedia(text: str) -> int:
    """统计百科类出现次数"""
    return len(ENCYCLOPEDIA_PATTERN.findall(text))


def count_revisions(text: str) -> int:
    """统计 `## 修订记录` 节下的 `### ` 子小节数"""
    idx = text.find(REVISION_HEADER)
    if idx < 0:
        return 0
    rest = text[idx + len(REVISION_HEADER):]
    # 截断到下一个同级 `## ` 或文末
    next_h2 = re.search(r"^## [^\n]+", rest, re.MULTILINE)
    body = rest[: next_h2.start()] if next_h2 else rest
    return len(re.findall(r"^### ", body, re.MULTILINE))


def count_effective_lines(text: str) -> int:
    """有效行数（去空白行）"""
    return sum(1 for ln in text.splitlines() if ln.strip())


def extract_header_label(text: str) -> tuple[str, dict] | tuple[None, None]:
    """从文件头部提取 `> **来源质量评估**：...` 整行。
    返回 (原始整行, 解析后的结构字典)。取第一个命中（通常是最新的）"""
    m = HEADER_LABEL_PATTERN.search(text)
    if not m:
        return None, None
    line = m.group(0).strip()
    body = m.group("body").strip()
    # 解析风险等级
    risk = None
    for key in ["高风险", "低-中风险", "中-低风险", "低风险", "中风险"]:
        if key in body:
            risk = key
            break
    parsed = {
        "raw_line": line,
        "risk": risk,
        "primary_declared": None,
        "enc_pct_declared": None,
    }
    pm = LABEL_PRIMARY_PATTERN.search(body)
    if pm:
        parsed["primary_declared"] = int(pm.group(1))
    em = LABEL_ENCYCLO_PCT_PATTERN.search(body)
    if em:
        for g in em.groups():
            if g:
                parsed["enc_pct_declared"] = int(g)
                break
    return line, parsed


def build_new_label(primary: int, primary_vol_unique: int, enc_cnt: int, enc_ratio: float, risk: str) -> str:
    """产出新标签建议行"""
    pct = round(enc_ratio * 100)
    return (
        f"> **来源质量评估**：{risk} | 一手正史: {primary} 条（去重卷号 {primary_vol_unique}） "
        f"| 百科/二手: {enc_cnt} 条（占 {pct}%） | 评估时间: {AUDIT_DATE}"
    )


def strip_header(text: str) -> str:
    """剥离文件头部标签/说明区：从开头到第一个 `## ` 二级标题前。
    这样可以避免头部标签里的示例文字（如"《明史》卷 222 等"）被计入正文统计。"""
    m = re.search(r"^## \S", text, re.MULTILINE)
    if m:
        return text[m.start():]
    return text


def audit_file(path: Path) -> dict:
    """审计单个文件"""
    raw_text = path.read_text(encoding="utf-8", errors="replace")
    # 正文：剥离头部标签区后再统计引用次数
    body = strip_header(raw_text)
    # 但行数/修订记录/头部标签解析仍然基于原文
    text = body
    primary = count_primary(text)
    primary_vol = count_unique_volumes(text)
    enc = count_encyclopedia(text)
    denom = primary + enc
    enc_ratio = (enc / denom) if denom > 0 else 0.0
    # 修订记录/行数/头部标签从完整原文读
    revisions = count_revisions(raw_text)
    lines = count_effective_lines(raw_text)
    risk_new = classify_risk(primary, enc_ratio)

    label_line, label_parsed = extract_header_label(raw_text)
    has_label = label_line is not None

    rel = path.relative_to(ROOT).as_posix()
    result = {
        "file": rel,
        "name": path.stem,
        "category": path.parent.name,
        "lines": lines,
        "revisions": revisions,
        "primary_count": primary,
        "primary_volumes_unique": primary_vol,
        "encyclopedia_count": enc,
        "encyclopedia_ratio_pct": round(enc_ratio * 100, 1),
        "new_risk": risk_new,
        "new_label_suggestion": build_new_label(primary, primary_vol, enc, enc_ratio, risk_new),
        "has_label": has_label,
        "old_label_raw": label_line,
        "old_risk": label_parsed["risk"] if label_parsed else None,
        "old_primary_declared": label_parsed["primary_declared"] if label_parsed else None,
        "old_enc_pct_declared": label_parsed["enc_pct_declared"] if label_parsed else None,
    }

    # 判定过期
    stale = False
    reasons = []
    if has_label:
        op = result["old_primary_declared"]
        oe = result["old_enc_pct_declared"]
        new_pct = round(enc_ratio * 100)
        if op is not None and abs(op - primary) > 5:
            stale = True
            reasons.append(f"一手卷次差 {primary - op:+d}（旧 {op}, 新 {primary}）")
        if oe is not None and abs(oe - new_pct) > 15:
            stale = True
            reasons.append(f"百科占比差 {new_pct - oe:+d}%（旧 {oe}%, 新 {new_pct}%）")
        # 旧风险等级与新不一致：若为简短标签（无数值）则以等级变化作为过期信号；
        # 若旧标签已有数值但等级仍不一致，也是过期（数值阈值可能未触发但等级跨档）
        if result["old_risk"] and result["old_risk"] != risk_new:
            # 对"低-中风险"/"中-低风险"这类中间态与"中风险"之间不视为严重变化
            loose_pair = {("低-中风险", "中风险"), ("中风险", "低-中风险"),
                          ("中-低风险", "中风险"), ("中风险", "中-低风险")}
            if (result["old_risk"], risk_new) not in loose_pair:
                stale = True
            reasons.append(f"风险等级 {result['old_risk']} → {risk_new}")

    result["stale"] = stale
    result["stale_reasons"] = reasons
    return result


def main():
    # 扫描所有 md 文件
    all_md = sorted(BIO_DIR.rglob("*.md"))
    targets = [p for p in all_md if not should_skip(p)]

    stale_labels = []
    up_to_date = []
    no_label = []
    all_results = []

    for p in targets:
        try:
            r = audit_file(p)
        except Exception as e:
            print(f"[warn] 处理失败 {p}: {e}")
            continue
        all_results.append(r)
        if not r["has_label"]:
            no_label.append(r)
        elif r["stale"]:
            stale_labels.append(r)
        else:
            up_to_date.append(r)

    # --- 产出 JSON ---
    VALID_DIR.mkdir(parents=True, exist_ok=True)
    summary = {
        "audit_time": AUDIT_DATE,
        "script": "tools/bio008_header_audit.py",
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "total_scanned": len(targets),
        "has_label_count": len(targets) - len(no_label),
        "stale_count": len(stale_labels),
        "up_to_date_count": len(up_to_date),
        "no_label_count": len(no_label),
        "stale_labels": [
            {
                "file": r["file"],
                "name": r["name"],
                "old_label": r["old_label_raw"],
                "new_label": r["new_label_suggestion"],
                "reason": "; ".join(r["stale_reasons"]) or "风险等级变化",
                "old_primary": r["old_primary_declared"],
                "new_primary": r["primary_count"],
                "old_enc_pct": r["old_enc_pct_declared"],
                "new_enc_pct": round(r["encyclopedia_ratio_pct"]),
                "old_risk": r["old_risk"],
                "new_risk": r["new_risk"],
            }
            for r in stale_labels
        ],
        "up_to_date": [
            {
                "file": r["file"],
                "name": r["name"],
                "risk": r["new_risk"],
                "primary": r["primary_count"],
                "enc_pct": round(r["encyclopedia_ratio_pct"]),
            }
            for r in up_to_date
        ],
        "no_label": [
            {
                "file": r["file"],
                "name": r["name"],
                "new_label_suggestion": r["new_label_suggestion"],
                "primary": r["primary_count"],
                "enc_pct": round(r["encyclopedia_ratio_pct"]),
                "new_risk": r["new_risk"],
            }
            for r in no_label
        ],
        "all_results": all_results,
    }
    OUT_JSON.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")

    # --- 产出 Markdown 报告 ---
    write_md_report(summary)

    print(f"[done] 扫描 {len(targets)} 份；过期 {len(stale_labels)}；最新 {len(up_to_date)}；无标签 {len(no_label)}")
    print(f"       JSON → {OUT_JSON.relative_to(ROOT).as_posix()}")
    print(f"       MD   → {OUT_MD.relative_to(ROOT).as_posix()}")


def classify_change(old_risk: str, new_risk: str) -> str:
    """风险等级变化方向分类"""
    order = {"低风险": 0, "低-中风险": 1, "中-低风险": 1, "中风险": 2, "高风险": 3}
    if old_risk not in order or new_risk not in order:
        return "未知"
    if order[new_risk] < order[old_risk]:
        return "改善"
    if order[new_risk] > order[old_risk]:
        return "退化"
    return "持平"


def write_md_report(summary: dict):
    """生成 Markdown 审计报告"""
    total = summary["total_scanned"]
    stale = summary["stale_labels"]
    no_lbl = summary["no_label"]
    up = summary["up_to_date"]

    # 按风险等级变化分类
    improvements = []
    degradations = []
    flat_changes = []
    for r in stale:
        change = classify_change(r["old_risk"] or "", r["new_risk"] or "")
        item = {
            **r,
            "change": change,
        }
        if change == "改善":
            improvements.append(item)
        elif change == "退化":
            degradations.append(item)
        else:
            flat_changes.append(item)

    lines = []
    lines.append(f"# BIO-008 头部标签审计报告（{summary['audit_time']}）")
    lines.append("")
    lines.append(f"- 脚本：`tools/bio008_header_audit.py`")
    lines.append(f"- 生成时间：{summary['generated_at']}")
    lines.append(f"- 扫描范围：`人物名录/**/*.md`（跳过 `_validation/`、`_backup_*/`、`INDEX.md`）")
    lines.append("")
    lines.append("## 总览")
    lines.append("")
    lines.append(f"- 扫描总数：**{total}** 份传记")
    lines.append(f"- 已有头部标签：{summary['has_label_count']} 份")
    lines.append(f"- 标签过期需刷新：**{summary['stale_count']}** 份")
    lines.append(f"- 标签最新（与实际统计一致）：{summary['up_to_date_count']} 份")
    lines.append(f"- 无标签（从未评估）：{summary['no_label_count']} 份")
    lines.append("")

    lines.append("## 判定阈值")
    lines.append("")
    lines.append("- 一手卷次数差异 > 5 → 过期")
    lines.append("- 百科占比差异 > 15 个百分点 → 过期")
    lines.append("- 任一命中即标记 `stale = true`；不够阈值的风险等级变化仅作参考记录")
    lines.append("")

    lines.append("## 分级标准（BIO-002 复用）")
    lines.append("")
    lines.append("- 低风险：一手卷次 ≥ 10 且 百科占比 < 30%")
    lines.append("- 中风险：一手卷次 3-9 或 百科占比 30-50%")
    lines.append("- 高风险：一手卷次 ≤ 2 或 百科占比 > 50%")
    lines.append("")

    lines.append("## 过期标签清单（按风险变化分组）")
    lines.append("")

    def render_table(items: list, title: str):
        if not items:
            return
        lines.append(f"### {title}（{len(items)} 份）")
        lines.append("")
        lines.append("| 人名 | 分类 | 旧风险 | 新风险 | 旧一手 | 新一手 | 旧百科% | 新百科% | 偏差原因 |")
        lines.append("|------|------|--------|--------|--------|--------|---------|---------|----------|")
        for it in sorted(items, key=lambda x: (x.get("new_risk") or "", x.get("name") or "")):
            # 查回 category
            cat = next((r["category"] for r in summary["all_results"] if r["file"] == it["file"]), "")
            lines.append(
                "| {name} | {cat} | {or_} | {nr} | {op} | {np_} | {oe} | {ne} | {reason} |".format(
                    name=it["name"], cat=cat,
                    or_=it.get("old_risk") or "—",
                    nr=it.get("new_risk") or "—",
                    op=it.get("old_primary") if it.get("old_primary") is not None else "—",
                    np_=it.get("new_primary"),
                    oe=f"{it.get('old_enc_pct')}%" if it.get("old_enc_pct") is not None else "—",
                    ne=f"{it.get('new_enc_pct')}%",
                    reason=it.get("reason") or "—",
                )
            )
        lines.append("")

    render_table(improvements, "改善（旧→新 风险等级下降）")
    render_table(degradations, "退化（旧→新 风险等级上升）")
    render_table(flat_changes, "持平但数值漂移")

    lines.append("## 无标签文件清单")
    lines.append("")
    if no_lbl:
        lines.append("| 人名 | 分类 | 一手 | 百科% | 建议风险 | 建议标签（节选） |")
        lines.append("|------|------|------|-------|----------|------------------|")
        for r in sorted(no_lbl, key=lambda x: x["name"]):
            cat = next((a["category"] for a in summary["all_results"] if a["file"] == r["file"]), "")
            lines.append(
                f"| {r['name']} | {cat} | {r['primary']} | {r['enc_pct']}% | {r['new_risk']} | `{r['new_label_suggestion'][:80]}...` |"
            )
        lines.append("")
    else:
        lines.append("（无）")
        lines.append("")

    lines.append("## 最新标签清单（样本摘要，前 15 条）")
    lines.append("")
    lines.append("| 人名 | 风险 | 一手 | 百科% |")
    lines.append("|------|------|------|-------|")
    for r in sorted(up, key=lambda x: x["name"])[:15]:
        lines.append(f"| {r['name']} | {r['risk']} | {r['primary']} | {r['enc_pct']}% |")
    if len(up) > 15:
        lines.append(f"| …… | 共 {len(up)} 份 | | |")
    lines.append("")

    lines.append("## 批量重算建议")
    lines.append("")
    stale_ratio = len(stale) / max(1, summary["has_label_count"])
    lines.append(f"- 过期率：{len(stale)}/{summary['has_label_count']} = **{stale_ratio:.0%}**")
    if stale_ratio >= 0.3:
        lines.append('- **建议：批量刷新**。过期率超过 30%，说明旧标签时的统计工具识别口径普遍偏窄（典型症状：`明史卷XXX` 简写未被识别）。')
        lines.append('  - 建议做法：由独立编辑代理按本报告 `new_label_suggestion` 字段批量替换文件头部 `> **来源质量评估**：...` 整行。')
        lines.append('  - 注意：不要覆盖"说明""后续修订方向"两条副说明行；仅替换 `> **来源质量评估**：...` 那一行。')
        lines.append('  - 注意：对"无标签"文件先走诊断单流程（BIO-002），确认一手/百科定义达成共识后再插入头部。')
    else:
        lines.append('- **建议：定点刷新**。过期率低于 30%，逐份按"过期清单"修订即可，不必批量重算。')
    lines.append("")
    lines.append("- **本脚本的定位**：审计 + 建议，不直接改文件。后续批量修订由独立编辑代理执行，避免与其他正在跑的代理冲突。")
    lines.append("")

    lines.append("## 脚本计算口径说明")
    lines.append("")
    lines.append('- **一手卷次数（primary_count）**：统计正文中《明史·XXX》/《明史》卷 N / `明史卷XXX` 等 14 类一手古籍 + 卷号引用的**出现次数**（每次命中+1）。与头部旧标签"一手 N 条"措辞对齐。')
    lines.append('- **去重卷号数（primary_volumes_unique）**：同一"书名+卷号"对只计一次。反映"来源面"广度。')
    lines.append('- **百科次数（encyclopedia_count）**：`百度百科` / `维基百科` / `搜狗百科` / `360百科` / `互动百科` / `wikipedia` 出现次数。')
    lines.append('- **百科占比**：`百科次数 / (百科次数 + 一手次数)`。与现有"占 38%""占 32%"标签口径一致。')
    lines.append('- **不计入分母**：笔记/论文/文集等其他书籍名（因识别难度高，暂不精确分类）。这使得"百科占比"实际是"纯二手百科 / (一手 + 百科)"的比例，偏保守。')
    lines.append("")
    lines.append("## 已知局限")
    lines.append("")
    lines.append('- 本脚本无法判别"一手古籍引用是否在被质疑的存疑区"——仅按文本出现统计。对"已降级为据晚出笔记"的条目仍会计为一手。')
    lines.append('- 行内嵌入式引用（如"据《明史》卷 219"）与方括号标注（`[来源：明史卷219]`）同等计数，不区分证据强度。')
    lines.append('- 对"《明史·XXX》"后没有明确卷号的提及，算 1 次一手但无法入"去重卷号"集合。')
    lines.append("")

    OUT_MD.write_text("\n".join(lines), encoding="utf-8")


if __name__ == "__main__":
    main()
