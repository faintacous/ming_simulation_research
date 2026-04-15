#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
人物传记校验流水线
==================

在 tools/batch_biography.py 跑完 Kimi 初稿后，自动串接 Codex 校验链，
把初稿提升到"尽可能详细的史实资料"标准。

设计文档：docs/biography_validation_pipeline_design.md

阶段：
    A 综合校验 → B 按工单修订 → C 联网核对 → D 跨文件一致性 → E 机器审计 → F 汇总

默认行为：等待 Kimi 批次（batch_biography_p1_20260415_130707.log）完成
后启动；已产出的阶段会跳过（幂等）。

用法：
    # 标准启动（等待 Kimi 完成 → 跑完整链路）
    python tools/validate_biographies.py

    # 跳过等待，立即启动
    python tools/validate_biographies.py --skip-wait

    # P2 批次复用
    python tools/validate_biographies.py --batch-id P2 \
        --persons-file p2.json --skip-wait

    # 强制重跑某阶段
    python tools/validate_biographies.py --skip-wait --force-stage C
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Optional

# ── 路径配置 ─────────────────────────────────────────
BASE_DIR = Path("G:/AIDev/Ming_Simulation_Research")
PERSONS_ROOT = BASE_DIR / "人物名录"
VALIDATION_ROOT = PERSONS_ROOT / "_validation"
LOG_DIR = BASE_DIR / "logs"
RUN_VALIDATION_PY = VALIDATION_ROOT / "tools" / "run_validation.py"

CODEX_BIN = os.environ.get("CODEX_BIN", "C:/Users/pc/AppData/Roaming/npm/codex.cmd")

# ── 超时策略 ─────────────────────────────────────────
TIMEOUT_A = 600    # 综合校验 600s/人
TIMEOUT_B = 900    # 修订 900s/人
TIMEOUT_C = 900    # 联网核对 900s/人
TIMEOUT_D = 1800   # 跨文件一致性（整批一次）
WAIT_POLL_INTERVAL = 300   # 5 分钟轮询一次
WAIT_MAX_SECONDS = 36000   # 10 小时最大等待

# ── P1 批次默认人物清单（与 tools/batch_biography.py 一致）─
DEFAULT_P1_PERSONS = [
    {"name": "张居正", "category": "阁臣重臣"},
    {"name": "海瑞", "category": "中下层文官"},
    {"name": "朱翊钧", "category": "帝王"},
    {"name": "王喜姐", "category": "后妃宗室"},
    {"name": "郑贵妃", "category": "后妃宗室"},
    {"name": "李时珍", "category": "文人学者"},
    {"name": "李太后", "category": "后妃宗室"},
    {"name": "麻贵", "category": "武将"},
    {"name": "谭纶", "category": "武将"},
    {"name": "李成梁", "category": "武将"},
    {"name": "努尔哈赤", "category": "外国人物"},
    {"name": "陈矩", "category": "宦官"},
    {"name": "顾宪成", "category": "中下层文官"},
]

DEFAULT_WAIT_LOG = BASE_DIR / "logs" / "batch_biography_p1_20260415_130707.log"

# ── 日志封装 ─────────────────────────────────────────
class Tee:
    def __init__(self, *streams):
        self.streams = streams

    def write(self, data):
        for s in self.streams:
            try:
                s.write(data)
                s.flush()
            except Exception:
                pass

    def flush(self):
        for s in self.streams:
            try:
                s.flush()
            except Exception:
                pass


def log(msg: str):
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print(f"[{ts}] {msg}", flush=True)


# ── Codex 调用 ───────────────────────────────────────
def call_codex(prompt: str, timeout: int, out_path: Path,
               sandbox: str = "read-only", stage_tag: str = "codex") -> dict:
    """复用 research_pipeline.run_codex 的调用格式。

    sandbox: "read-only"（A/C/D 阶段）或 "workspace-write"（B 阶段，需要写文件）
    """
    out_path.parent.mkdir(parents=True, exist_ok=True)
    if out_path.exists():
        try:
            out_path.unlink()
        except Exception:
            pass

    cmd = [
        CODEX_BIN, "exec",
        "--skip-git-repo-check",
        "--sandbox", sandbox,
        "--ephemeral",
        "-o", str(out_path),
        prompt,
    ]

    start = time.time()
    log(f"  [{stage_tag}] Codex 调用开始（sandbox={sandbox}，timeout={timeout}s）")
    try:
        result = subprocess.run(
            cmd, capture_output=True, text=True,
            timeout=timeout, cwd=str(BASE_DIR),
            encoding="utf-8", errors="replace",
        )
        elapsed = time.time() - start

        if out_path.exists() and out_path.stat().st_size > 0:
            content = out_path.read_text(encoding="utf-8", errors="replace")
            lines = len(content.splitlines())
            log(f"  [{stage_tag}] Codex 完成：{lines} 行，{elapsed:.0f}s")
            return {"success": True, "lines": lines, "elapsed": elapsed,
                    "content": content, "returncode": result.returncode}
        else:
            stderr_tail = (result.stderr or "")[-500:]
            log(f"  [{stage_tag}] Codex 失败（未产出文件）: {stderr_tail[:200]}")
            return {"success": False, "elapsed": elapsed,
                    "error": "no_output_file",
                    "stderr_tail": stderr_tail,
                    "returncode": result.returncode}

    except subprocess.TimeoutExpired:
        elapsed = time.time() - start
        log(f"  [{stage_tag}] Codex 超时（{elapsed:.0f}s）")
        return {"success": False, "elapsed": elapsed, "error": "timeout"}
    except Exception as e:
        elapsed = time.time() - start
        log(f"  [{stage_tag}] Codex 异常: {e}")
        return {"success": False, "elapsed": elapsed, "error": str(e)}


# ── 阶段 0：等待 Kimi 批次完成 ──────────────────────
def wait_for_kimi_batch(log_path: Path, skip: bool) -> bool:
    if skip:
        log("跳过等待（--skip-wait）")
        return True

    if not log_path.exists():
        log(f"等待日志不存在: {log_path}，继续（假定已完成）")
        return True

    log(f"等待 Kimi 批次完成，监控日志: {log_path}")
    start = time.time()
    while True:
        try:
            content = log_path.read_text(encoding="utf-8", errors="replace")
            tail = content[-5000:]
            # 完成标志：batch_biography.py 的 print 语句 "完成！总耗时"
            if "完成！总耗时" in tail:
                log("检测到 Kimi 批次完成标志（'完成！总耗时'）")
                time.sleep(30)  # 等文件落盘稳定
                return True
        except Exception as e:
            log(f"  读取日志异常: {e}")

        elapsed = time.time() - start
        if elapsed > WAIT_MAX_SECONDS:
            log(f"等待超时（{elapsed:.0f}s），假定已完成，继续")
            return True

        log(f"  等待中...（已 {elapsed/60:.1f} 分钟，下次检查在 {WAIT_POLL_INTERVAL}s 后）")
        time.sleep(WAIT_POLL_INTERVAL)


# ── 阶段 A：综合校验 ─────────────────────────────────
def stage_a_audit(persons: list, batch_dir: Path, force: bool) -> dict:
    log(f"=== 阶段 A：Codex 综合校验（13 人，串行）===")
    results = {}
    for idx, p in enumerate(persons, 1):
        name, category = p["name"], p["category"]
        person_file = PERSONS_ROOT / category / f"{name}.md"
        ticket_file = batch_dir / f"{name}_工单.md"

        if not person_file.exists():
            log(f"  [{idx}/{len(persons)}] {name}: 源文件不存在 {person_file}，跳过")
            results[name] = {"status": "missing_source"}
            continue

        if ticket_file.exists() and ticket_file.stat().st_size > 0 and not force:
            log(f"  [{idx}/{len(persons)}] {name}: 工单已存在，跳过")
            results[name] = {"status": "skipped"}
            continue

        log(f"--- [{idx}/{len(persons)}] {name} ---")
        prompt = build_prompt_a(name, category, person_file)
        res = call_codex(prompt, TIMEOUT_A, ticket_file,
                         sandbox="read-only", stage_tag=f"A-{name}")
        if res["success"]:
            results[name] = {"status": "ok", "lines": res["lines"],
                             "elapsed": res["elapsed"]}
        else:
            results[name] = {"status": "fail", "error": res.get("error")}
            write_failure_record(batch_dir, name, "A", res)

    return results


def build_prompt_a(name: str, category: str, person_file: Path) -> str:
    return f"""你是一名严谨的明史校验专家。请对以下人物传记做综合校验，找出其中的问题并产出结构化工单。

**目标文件**：`{person_file.as_posix()}`（人物：{name}，分类：{category}）

**校验任务**：
1. 请先用 Read 打开该文件完整读完。
2. 按以下 6 类检查，找出所有问题（不要偷懒，不要只列典型）：
   - R1 一手依据不足：关键事实是否有≥2 条一手或高等级二手依据
   - R2 争议定论化：正文（非"存疑"章节）是否把尚有争议的说法写成了定论
   - R3 未决争议缺失：是否有"存疑 & 待查"章节且含实质条目
   - R4 来源汇总不足：是否 ≥ 5 条来源且古籍注明卷次
   - R5 基本信息完整性：生卒/字号/籍贯/身份 是否 ≥ 3 项且无自相矛盾
   - R6 结构完整性：必备章节（基本信息 / 生平概要 / 关键事迹 / 存疑 & 待查 / 来源汇总）是否齐备
3. 额外检查：
   - 同文件内是否有自相矛盾（同一事件、同一人物关系在不同段落说法不一）
   - 关键事迹是否按时间排序、数量是否 ≥ 20 条
   - 是否引用了一手古籍的具体卷次（《明史》卷 X、《明实录》某年某月等）

**输出格式（严格遵守）**：

```
# {name} — 综合校验工单

## 概要
- 目标文件：{person_file.as_posix()}
- 校验时间：（填当前时间）
- 问题总数：N 条（严重 X / 中等 Y / 一般 Z）
- 结论：通过 / 需修订 / 严重不合规

## 问题清单

### BIO-AUDIT-001（严重）
- 规则：R2（争议定论化）
- 问题行：第 XX 行："（原文摘引）"
- 问题描述：（说明为什么是问题）
- 建议修订动作：（明确告诉下游修订者怎么改，例如"把 XX 句改为：XX"）
- 依据：（引用的一手史料或核实方式）

### BIO-AUDIT-002（中等）
...

## 未能判定需要联网核对的条目
- 条目 X：（待 C 阶段核对）
- ...

## 修订优先级
1. 必须修订（严重）：BIO-AUDIT-001, 003, 007
2. 建议修订（中等）：BIO-AUDIT-002, 004
3. 可以保留（一般）：其余
```

**硬约束**：
- 工单必须基于你"实际打开"的文件内容，不要按训练记忆补空。
- 每条问题必须有"问题行"锚定（行号或完整原文引用）。
- 建议修订动作必须具体到"改成什么"，不要只说"需要改"。
- 如果文件本身质量很高，没有问题，也要输出"## 问题清单\\n（本文件未发现问题）"。
"""


# ── 阶段 B：按工单修订 ───────────────────────────────
def stage_b_revise(persons: list, batch_dir: Path, force: bool) -> dict:
    log(f"=== 阶段 B：Codex 按工单修订（13 人，串行）===")
    results = {}
    for idx, p in enumerate(persons, 1):
        name, category = p["name"], p["category"]
        person_file = PERSONS_ROOT / category / f"{name}.md"
        ticket_file = batch_dir / f"{name}_工单.md"
        record_file = batch_dir / f"{name}_修订记录.md"

        if not ticket_file.exists():
            log(f"  [{idx}/{len(persons)}] {name}: 工单不存在，跳过修订")
            results[name] = {"status": "no_ticket"}
            continue

        if record_file.exists() and record_file.stat().st_size > 0 and not force:
            log(f"  [{idx}/{len(persons)}] {name}: 修订记录已存在，跳过")
            results[name] = {"status": "skipped"}
            continue

        log(f"--- [{idx}/{len(persons)}] {name} ---")
        prompt = build_prompt_b(name, category, person_file, ticket_file, record_file)
        res = call_codex(prompt, TIMEOUT_B, record_file,
                         sandbox="workspace-write", stage_tag=f"B-{name}")
        if res["success"]:
            results[name] = {"status": "ok", "elapsed": res["elapsed"]}
        else:
            results[name] = {"status": "fail", "error": res.get("error")}
            write_failure_record(batch_dir, name, "B", res)

    return results


def build_prompt_b(name: str, category: str, person_file: Path,
                   ticket_file: Path, record_file: Path) -> str:
    return f"""你是一名明史条目编辑。请按给定的工单对传记文件做精确修订。

**输入**：
- 传记文件（要修订）：`{person_file.as_posix()}`
- 校验工单（告诉你哪里要改）：`{ticket_file.as_posix()}`

**输出**：
- 原地覆盖传记文件（使用 Edit/Write 工具）
- 修订记录写入：`{record_file.as_posix()}`（使用 Write 工具）

**修订原则**：
1. **只修订工单中列出的问题**，不要改动其他内容。工单没说要改的，保持原样。
2. **基本信息中的争议性内容必须降级为"存疑"**，不能在基本信息里下定论（例如族属、未证的生卒日期等）。
3. **争议定论化（R2）的修订方式**：把肯定式叙述改为"一说 / 有说法认为 / 存疑"式表述，并在"存疑 & 待查"章节补一条对应条目。
4. **一手依据不足（R1）的修订方式**：如果工单给出了具体卷次引用，直接补入；如果没给，改为保守表述并在"存疑"章节列出待查项。
5. **不要为了修订而编造新的史料引用**。找不到的就写"存疑/待考"。

**修订记录格式**（写到 {record_file.as_posix()}）：

```
# {name} — 修订记录

## 概要
- 工单文件：{ticket_file.as_posix()}
- 修订文件：{person_file.as_posix()}
- 修订时间：（填当前时间）
- 处理问题数：N 条（采纳 X / 部分采纳 Y / 未采纳 Z）

## 逐条处理

### BIO-AUDIT-001（采纳）
- 原问题：（简述）
- 修订动作：（说明你改了什么）
- 修订前（原文）：
  > XXXX
- 修订后（新文）：
  > XXXX

### BIO-AUDIT-002（部分采纳）
- 原问题：（简述）
- 修订动作：（说明你只改了一部分，为什么）
- ...

### BIO-AUDIT-003（未采纳）
- 原问题：（简述）
- 未采纳理由：（例如"工单建议的修订与另一条工单冲突"、"缺乏更好的替代表述"）

## 验收自测
- BIO-001（结构完整性）：通过 / 不通过
- BIO-003（争议下沉）：通过 / 不通过
- BIO-006（六条规则）：各条状态
```

**硬约束**：
- 修订必须基于你实际读到的文件内容。
- 不要大规模重写，目标是"外科手术式"精确修订。
- 每处修订都要在记录里留痕（原文 + 新文对比）。
- 完成后在记录文件末尾写明"修订已落盘到 {person_file.as_posix()}"。
"""


# ── 阶段 C：联网核对 ─────────────────────────────────
def stage_c_crosscheck(persons: list, batch_dir: Path, force: bool) -> dict:
    log(f"=== 阶段 C：Codex 联网核对（13 人，串行）===")
    results = {}
    for idx, p in enumerate(persons, 1):
        name, category = p["name"], p["category"]
        person_file = PERSONS_ROOT / category / f"{name}.md"
        out_file = batch_dir / f"{name}_联网核对.md"

        if not person_file.exists():
            log(f"  [{idx}/{len(persons)}] {name}: 源文件不存在，跳过")
            results[name] = {"status": "missing_source"}
            continue

        if out_file.exists() and out_file.stat().st_size > 0 and not force:
            log(f"  [{idx}/{len(persons)}] {name}: 联网核对已存在，跳过")
            results[name] = {"status": "skipped"}
            continue

        log(f"--- [{idx}/{len(persons)}] {name} ---")
        prompt = build_prompt_c(name, category, person_file)
        res = call_codex(prompt, TIMEOUT_C, out_file,
                         sandbox="read-only", stage_tag=f"C-{name}")
        if res["success"]:
            results[name] = {"status": "ok", "lines": res["lines"],
                             "elapsed": res["elapsed"]}
        else:
            results[name] = {"status": "fail", "error": res.get("error")}
            write_failure_record(batch_dir, name, "C", res)

    return results


def build_prompt_c(name: str, category: str, person_file: Path) -> str:
    return f"""你是一名严谨的明史原文核对员。请对以下人物传记做联网核对。

**目标文件**：`{person_file.as_posix()}`（人物：{name}）

**任务流程**：
1. 先用 Read 打开该文件，挑出 10-15 条最可能出错、或最关键的事实性陈述（例如：生卒日、重大事件年份、官职任命、争议事件的定性、族属、数字类数据）。
2. 对每条陈述，用 WebFetch 到下列站点核对原文：
   - `https://zh.wikisource.org/wiki/明史/卷XXX`（《明史》）
   - `https://zh.wikisource.org/wiki/明史紀事本末/卷XXX`（《明史纪事本末》）
   - `https://ctext.org/datawiki.pl?if=gb&remap=gb&res=XXXX`（中国哲学书电子化计划）
   - `https://cbdb.fas.harvard.edu/cbdbapi/person.php?id=XXXX&o=html`（CBDB）
3. 逐条给出核对结论。

**硬约束**（必须遵守）：
- 你必须**以实际打开的页面内容为准**，不要按训练记忆补空。
- 如果某条事实在你打开的页面里找不到支撑，必须写"未证 / 待考"，不要编造。
- 每条结论必须附上你实际访问的 URL。
- 不确定时写"待继续翻某某卷"，不要硬下结论。

**输出格式**：

```
# {name} — 联网核对报告

## 概要
- 目标文件：{person_file.as_posix()}
- 核对时间：（填当前时间）
- 核对条目数：N 条（已证 X / 未证 Y / 需修订 Z）

## 逐条核对

### 陈述 1：（原文引用）
- 文件位置：第 XX 行
- 核对页面：<URL>
- 原文依据：（页面中与该陈述相关的原文片段）
- 结论：已证 / 未证 / 需修订
- 建议修订（如需）：（具体改成什么）

### 陈述 2：...
...

## 总体判断
- 高可信度内容：N 条
- 需标注"存疑"的内容：N 条
- 需直接修订的内容：N 条（将在下轮修订时处理）

## 本次访问的一手史料页面
- <URL 1>
- <URL 2>
- ...
```

**风格参考**：`人物名录/_validation/联网核对报告.md`（历史上已跑过的同类报告）。保持那种"以实际打开的页面为准"的锚定风格。
"""


# ── 阶段 D：跨文件一致性 ─────────────────────────────
def stage_d_crossfile(persons: list, batch_dir: Path, force: bool) -> dict:
    log(f"=== 阶段 D：Codex 跨文件一致性检查（BIO-005）===")
    out_file = batch_dir / "BIO-005_交叉一致性报告.md"

    if out_file.exists() and out_file.stat().st_size > 0 and not force:
        log(f"  跨文件报告已存在，跳过")
        return {"status": "skipped"}

    prompt = build_prompt_d(persons)
    res = call_codex(prompt, TIMEOUT_D, out_file,
                     sandbox="read-only", stage_tag="D-crossfile")
    if res["success"]:
        return {"status": "ok", "lines": res["lines"], "elapsed": res["elapsed"]}
    else:
        write_failure_record(batch_dir, "_crossfile", "D", res)
        return {"status": "fail", "error": res.get("error")}


def build_prompt_d(persons: list) -> str:
    file_list = "\n".join(
        f"- {p['name']}: `{(PERSONS_ROOT / p['category'] / (p['name'] + '.md')).as_posix()}`"
        for p in persons
    )
    return f"""你是一名跨条目一致性审查员。请对 P1 批次 13 份人物传记做 BIO-005 跨文件一致性检查。

**目标文件集合**：
{file_list}

**任务**：
1. 用 Read 逐个打开上述 13 份文件。
2. 重点检查以下**已知交叉热点**，找出不同文件中对同一事件的叙述差异：
   - **李成梁 ↔ 努尔哈赤**：
     - 1574 古勒寨之战细节
     - 1583 父祖死亡事件
     - "收养说 / 义子说" 是否一致
   - **国本之争**（张居正 / 朱翊钧 / 王喜姐 / 郑贵妃 / 李太后 五视角）：
     - 争议起讫年份
     - 郑贵妃角色定性
     - 各人物在事件中的立场
   - **朝鲜之役**（麻贵 / 谭纶 / 李成梁）：
     - 麻贵的族属说法
     - 三人在战役中的分工
   - **高拱被逐**（相关各篇）：
     - "十岁太子"戏剧化引语的定性（定论 / 存疑）
   - **梃击案**（郑贵妃 + 相关）：
     - 郑贵妃是否为幕后主使的证据边界
     - 庞保、刘成结局的叙述差异
3. 此外主动扫描其他可能的交叉冲突（例如张居正改革时麻贵/谭纶/李成梁的相关叙述）。

**输出格式**：

```
# P1 批次 BIO-005 交叉一致性报告

## 概要
- 检查文件数：13
- 发现交叉冲突：N 组（严重 X / 中等 Y / 一般 Z）

## 冲突清单

### 冲突 1（严重）：1583 年李成梁与努尔哈赤父祖死亡事件
- 涉及文件：
  - 李成梁.md 第 XX 行："原文引用"
  - 努尔哈赤.md 第 XX 行："原文引用"
- 冲突描述：（说明两者不一致在哪里）
- 建议统一标准：（给出应该统一为的表述）
- 建议修订行动：（列出每个文件分别该怎么改）

### 冲突 2（中等）：...
...

## 一致性良好的交叉点（已对齐，供记录）
- ...

## 建议的下一步行动
1. 以"统一标准"为基准，对 X/Y/Z 三处冲突做同步修订
2. ...
```

**硬约束**：
- 基于你实际打开的文件内容作答，不要按训练记忆补空。
- 每条冲突必须锚定到具体文件的具体行号 + 原文引用。
- "建议统一标准" 必须是可执行的（例如"统一写成：1583 年二月，古勒寨之役中父祖被杀"，而不是"应保持一致"）。
- 风格参考：`人物名录/_validation/交叉一致性报告.md`。
"""


# ── 阶段 E：机器审计 ─────────────────────────────────
def stage_e_audit() -> dict:
    log(f"=== 阶段 E：BIO-001/003/006 机器审计 ===")
    cmd = [
        "python", str(RUN_VALIDATION_PY),
        "--base-dir", str(PERSONS_ROOT),
        "--all",
    ]
    start = time.time()
    try:
        result = subprocess.run(
            cmd, capture_output=True, text=True,
            timeout=300, cwd=str(BASE_DIR),
            encoding="utf-8", errors="replace",
        )
        elapsed = time.time() - start
        log(f"  机器审计完成：returncode={result.returncode}，{elapsed:.0f}s")
        log(f"  stdout 末尾：\n{(result.stdout or '')[-800:]}")
        return {"status": "ok" if result.returncode == 0 else "partial",
                "returncode": result.returncode,
                "stdout_tail": (result.stdout or "")[-800:],
                "elapsed": elapsed}
    except Exception as e:
        elapsed = time.time() - start
        log(f"  机器审计异常: {e}")
        return {"status": "fail", "error": str(e), "elapsed": elapsed}


# ── 阶段 F：汇总报告 ─────────────────────────────────
def stage_f_summary(batch_id: str, batch_dir: Path, persons: list,
                    stage_results: dict, start_time: float):
    log(f"=== 阶段 F：生成综合验收报告 ===")
    report_file = batch_dir / "综合验收报告.md"

    lines = [
        f"# {batch_id} 批次综合验收报告",
        "",
        f"- 生成时间：{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
        f"- 批次人数：{len(persons)}",
        f"- 总耗时：{(time.time() - start_time) / 60:.1f} 分钟",
        "",
        "## 阶段概览",
        "",
        "| 阶段 | 状态 |",
        "|------|------|",
    ]

    for stage, label in [("A", "综合校验"), ("B", "按工单修订"),
                         ("C", "联网核对"), ("D", "跨文件一致性"),
                         ("E", "机器审计")]:
        res = stage_results.get(stage, {})
        if stage in ("A", "B", "C"):
            # 每人一条
            ok = sum(1 for v in res.values() if isinstance(v, dict) and v.get("status") == "ok")
            total = len(res)
            status_str = f"{ok}/{total} 成功"
        elif stage == "D":
            status_str = res.get("status", "unknown")
        elif stage == "E":
            status_str = f"returncode={res.get('returncode', '?')}，{res.get('status', 'unknown')}"
        else:
            status_str = "unknown"
        lines.append(f"| {stage} {label} | {status_str} |")

    lines.extend([
        "",
        "## 每人详情",
        "",
        "| 人物 | A 工单 | B 修订 | C 联网核对 | 工单文件 | 核对文件 |",
        "|------|--------|--------|------------|----------|----------|",
    ])
    for p in persons:
        name = p["name"]
        a = stage_results.get("A", {}).get(name, {}).get("status", "—")
        b = stage_results.get("B", {}).get(name, {}).get("status", "—")
        c = stage_results.get("C", {}).get(name, {}).get("status", "—")
        ticket = batch_dir / f"{name}_工单.md"
        crosscheck = batch_dir / f"{name}_联网核对.md"
        ticket_link = f"[{name}_工单.md]({ticket.name})" if ticket.exists() else "—"
        crosscheck_link = f"[{name}_联网核对.md]({crosscheck.name})" if crosscheck.exists() else "—"
        lines.append(f"| {name} | {a} | {b} | {c} | {ticket_link} | {crosscheck_link} |")

    # BIO-005 报告引用
    crossfile = batch_dir / "BIO-005_交叉一致性报告.md"
    lines.extend([
        "",
        "## 跨文件一致性（BIO-005）",
        "",
        f"- 报告：{'[BIO-005_交叉一致性报告.md](BIO-005_交叉一致性报告.md)' if crossfile.exists() else '未生成'}",
        "",
        "## 机器审计（BIO-001/003/006）",
        "",
        "报告位于 `人物名录/_validation/` 根目录：",
        "- `BIO-001结构检查报告.md`",
        "- `BIO-003扫描报告.md`",
        "- `BIO-006验收报告.md`",
        "- `BIO-006审计结果.json`",
        "",
    ])

    # 机器审计 stdout 末尾
    e_res = stage_results.get("E", {})
    if e_res.get("stdout_tail"):
        lines.extend([
            "### 机器审计输出末尾",
            "",
            "```",
            e_res["stdout_tail"],
            "```",
            "",
        ])

    # 失败记录
    failures = list(batch_dir.glob("*_失败记录.md"))
    if failures:
        lines.extend([
            "## 失败记录",
            "",
        ])
        for f in failures:
            lines.append(f"- [{f.name}]({f.name})")
        lines.append("")

    lines.extend([
        "## 下一步建议",
        "",
        "1. 人工审阅 BIO-006 验收报告，确认所有 13 人达到 '全合规' 或 '合格偏短'",
        "2. 对仍存在严重不合规（R1/R2 失败）的人物，回到 Kimi/Codex 流水线重跑",
        "3. 对 BIO-005 交叉冲突，按建议统一标准做同步修订",
        "4. 更新 `sources.md` 和 `devlog.md`",
        "",
        "---",
        "",
        f"*本报告由 `tools/validate_biographies.py` 于 {datetime.now().strftime('%Y-%m-%d %H:%M:%S')} 生成*",
    ])

    report_file.write_text("\n".join(lines), encoding="utf-8")
    log(f"  综合验收报告已生成: {report_file}")
    return report_file


# ── 工具函数 ─────────────────────────────────────────
def write_failure_record(batch_dir: Path, name: str, stage: str, result: dict):
    record = batch_dir / f"{name}_失败记录.md"
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    # append 模式：同一人物多阶段失败时累加
    existing = record.read_text(encoding="utf-8") if record.exists() else ""
    content = existing + f"""
## 阶段 {stage} 失败（{ts}）
- 错误：{result.get('error', 'unknown')}
- 耗时：{result.get('elapsed', 0):.0f}s
- stderr 末尾：
```
{result.get('stderr_tail', '(无)')}
```
"""
    if not existing:
        content = f"# {name} 校验失败记录\n" + content
    record.write_text(content, encoding="utf-8")


def load_persons(persons_file: Optional[str]) -> list:
    if persons_file:
        with open(persons_file, encoding="utf-8") as f:
            return json.load(f)
    return DEFAULT_P1_PERSONS


# ── 主函数 ───────────────────────────────────────────
def main():
    parser = argparse.ArgumentParser(description="人物传记校验流水线")
    parser.add_argument("--batch-id", default="P1", help="批次标识（默认 P1）")
    parser.add_argument("--persons-file", default=None,
                        help="人物清单 JSON（默认使用 P1 硬编码清单）")
    parser.add_argument("--skip-wait", action="store_true",
                        help="跳过等待 Kimi 批次完成")
    parser.add_argument("--wait-log", default=str(DEFAULT_WAIT_LOG),
                        help="监控的 Kimi 批次日志路径")
    parser.add_argument("--force-stage", default=None,
                        choices=["A", "B", "C", "D"],
                        help="强制重跑某阶段（忽略已产出的幂等性）")
    parser.add_argument("--only-stage", default=None,
                        choices=["A", "B", "C", "D", "E", "F"],
                        help="只跑某个阶段（调试用）")
    args = parser.parse_args()

    # 初始化日志
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    main_log = LOG_DIR / f"validate_biographies_{args.batch_id}_main_{ts}.log"
    log_fh = open(main_log, "w", encoding="utf-8")
    sys.stdout = Tee(sys.__stdout__, log_fh)
    sys.stderr = Tee(sys.__stderr__, log_fh)

    log(f"主日志: {main_log}")
    log(f"批次 ID: {args.batch_id}")

    # 批次目录
    batch_date = datetime.now().strftime("%Y%m%d")
    batch_dir = VALIDATION_ROOT / f"{args.batch_id}_批次_{batch_date}"
    batch_dir.mkdir(parents=True, exist_ok=True)
    log(f"批次目录: {batch_dir}")

    # 载入人物清单
    persons = load_persons(args.persons_file)
    log(f"人物数: {len(persons)}")

    # 阶段 0：等待 Kimi
    wait_for_kimi_batch(Path(args.wait_log), args.skip_wait)

    start_time = time.time()
    stage_results = {}

    stages_to_run = [args.only_stage] if args.only_stage else ["A", "B", "C", "D", "E", "F"]

    if "A" in stages_to_run:
        stage_results["A"] = stage_a_audit(
            persons, batch_dir, force=(args.force_stage == "A")
        )
    if "B" in stages_to_run:
        stage_results["B"] = stage_b_revise(
            persons, batch_dir, force=(args.force_stage == "B")
        )
    if "C" in stages_to_run:
        stage_results["C"] = stage_c_crosscheck(
            persons, batch_dir, force=(args.force_stage == "C")
        )
    if "D" in stages_to_run:
        stage_results["D"] = stage_d_crossfile(
            persons, batch_dir, force=(args.force_stage == "D")
        )
    if "E" in stages_to_run:
        stage_results["E"] = stage_e_audit()
    if "F" in stages_to_run:
        stage_f_summary(args.batch_id, batch_dir, persons, stage_results, start_time)

    total = time.time() - start_time
    log(f"")
    log(f"=== 流水线完成，总耗时 {total/60:.1f} 分钟 ===")
    log_fh.close()


if __name__ == "__main__":
    main()
