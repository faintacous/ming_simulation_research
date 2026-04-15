"""
Kimi 批量搜集人物传记（串行版，鲁棒版）
=======================================
- 使用 tools/research_pipeline.py 的 --kimi-only 模式
- 自定义详细 prompt（含内容密度硬约束）
- 严格串行执行，避免并发触发 Kimi 速率限制
- 完成后自动移动到 人物名录/{category}/{name}.md
- 同时把所有 print 输出写入 logs/batch_biography_p1_{tag}_{YYYYMMDD_HHMMSS}.log

鲁棒性强化（2026-04-15）：
- 强制 stdout/stderr UTF-8（避免 Windows 控制台 GBK 乱码）
- subprocess.Popen + 实时读 stdout，避免大输出 capture_output 缓冲死锁
- 心跳文件 tools/batch_biography.status：每人开始/结束/每 30 秒更新，外界可见进程存活
- 绝对路径引用 research_pipeline.py（避免 cwd 误配）
- 每个 print 都 flush=True
- 完成列表持久化到 JSON，支持断点续跑（--resume 只跑尚未完成的）

用法：
    python tools/batch_biography.py                # 跑全部 13 人
    python tools/batch_biography.py --resume       # 只跑 status 里未完成的
    python tools/batch_biography.py --tag rerun    # 日志文件名带 tag
"""
from __future__ import annotations

import argparse
import io
import json
import os
import shutil
import subprocess
import sys
import threading
import time
from datetime import datetime
from pathlib import Path

# ── 强制 UTF-8，避免 Windows GBK 控制台乱码 ──────────────
os.environ.setdefault("PYTHONIOENCODING", "utf-8")
os.environ.setdefault("PYTHONUTF8", "1")
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    # 兜底：对无 reconfigure 的环境用 TextIOWrapper 重建
    try:
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace", line_buffering=True)
        sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace", line_buffering=True)
    except Exception:
        pass

# ── 路径配置（全部绝对路径）──────────────────────────────
BASE_DIR = Path("G:/AIDev/Ming_Simulation_Research")
TOOLS_DIR = BASE_DIR / "tools"
RESEARCH_PIPELINE = TOOLS_DIR / "research_pipeline.py"
KIMI_OUTPUT_DIR = BASE_DIR / "角色视角"
TARGET_DIR = BASE_DIR / "人物名录"
LOG_DIR = BASE_DIR / "logs"
STATUS_FILE = TOOLS_DIR / "batch_biography.status"
RESULTS_FILE = TOOLS_DIR / "batch_biography.results.json"

TIMEOUT = 1800  # 与 tools/research_pipeline.py 的 Kimi 超时对齐

PERSONS = [
    ("张居正", "内阁首辅（1572-1582），万历前期改革核心推手，一条鞭法、考成法主导者", "阁臣重臣"),
    ("海瑞", "应天巡抚、南京右都御史，以清廉直言闻名", "中下层文官"),
    ("朱翊钧", "明神宗/万历帝，在位 48 年（1572-1620），前期张居正辅政、后期怠政", "帝王"),
    ("王喜姐", "明神宗孝端显皇后，万历原配，国本之争中立场关键", "后妃宗室"),
    ("郑贵妃", "万历宠妃，朱常洵生母，国本之争焦点", "后妃宗室"),
    ("李时珍", "医学家，《本草纲目》作者", "文人学者"),
    ("李太后", "慈圣皇太后，明神宗生母，万历初年辅政", "后妃宗室"),
    ("麻贵", "总兵/提督，朝鲜之役第二次入朝主帅", "武将"),
    ("谭纶", "蓟辽保定总督、兵部尚书，戚继光的直接上级", "武将"),
    ("李成梁", "辽东总兵，镇守辽东三十年，努尔哈赤早年曾受其庇护", "武将"),
    ("努尔哈赤", "后金大汗，建州女真统一者，八旗制度建立者", "外国人物"),
    ("陈矩", "司礼监掌印太监，万历朝贤宦代表", "宦官"),
    ("顾宪成", "吏部文选司郎中，东林书院创始人，东林党精神领袖", "中下层文官"),
]

PROMPT_TEMPLATE = """研究明朝人物{name}（{identity}）的完整生平档案。聚焦其在1550-1600年间的活动，但完整覆盖其一生。

【内容密度要求】（硬约束）
- 必须查阅至少 5 个独立来源：≥ 2 个古籍卷次 + ≥ 2 个百科条目 + ≥ 1 个其他
- 关键事迹至少 20 条，按时间顺序
- 存疑区至少列 5 条待考问题

【必填章节】
- 家族背景与师承
- 主要官职/事业阶段
- 性格与逸闻轶事
- 重要著作/战功/政绩
- 人际网络（友、敌、门生）

请按以下格式输出：

# {name}

## 基本信息
- 生卒：（精确到年月日，如可考）
- 字号：
- 籍贯：
- 身份/官职：（列出主要官职变迁）

## 生平概要
（800-1200字的完整叙述）

## 关键事迹
（按时间顺序至少20条，每条标注来源）
- 年份：事迹 [来源：《xxx》卷xx 或 URL]

## 重要人际关系
- 家族：
- 师承/门生：
- 盟友/同党：
- 对手/政敌：

## 主要著作/战功/政绩

## 性格与逸闻
（至少3条，每条标注来源）

## 历史评价
### 时人评价
### 后世评价

## 存疑 & 待查
（至少5条）

## 来源汇总
（至少5个独立来源，古籍须注明卷次）
"""


# ── 日志 Tee ─────────────────────────────────────────
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


# ── 心跳状态文件 ─────────────────────────────────────
_heartbeat_stop = threading.Event()
_heartbeat_state = {"phase": "init", "current": "", "pid": os.getpid()}


def update_status(phase: str = None, current: str = None, extra: dict = None):
    if phase is not None:
        _heartbeat_state["phase"] = phase
    if current is not None:
        _heartbeat_state["current"] = current
    if extra:
        _heartbeat_state.update(extra)
    _heartbeat_state["last_update"] = datetime.now().isoformat()
    try:
        STATUS_FILE.write_text(json.dumps(_heartbeat_state, ensure_ascii=False, indent=2),
                               encoding="utf-8")
    except Exception:
        pass


def heartbeat_loop():
    """后台心跳：每 30s 刷新 status 文件的 last_update，证明父进程活着。"""
    while not _heartbeat_stop.wait(30):
        update_status()


# ── Kimi 调用（subprocess.Popen + 实时流式读取）─────
def run_person(name: str, identity: str, category: str) -> dict:
    prompt = PROMPT_TEMPLATE.format(name=name, identity=identity)
    cmd = [
        sys.executable, "-u",  # -u 强制 subprocess 无缓冲
        str(RESEARCH_PIPELINE),
        name,
        "--kimi-only",
        "--prompt", prompt,
    ]
    update_status(phase="running", current=name, extra={"category": category, "started_at": datetime.now().isoformat()})
    start = time.time()

    env = os.environ.copy()
    env["PYTHONUTF8"] = "1"
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONUNBUFFERED"] = "1"

    log(f"  [Popen] 启动 research_pipeline.py --kimi-only {name}")

    try:
        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            cwd=str(BASE_DIR),
            env=env,
            encoding="utf-8",
            errors="replace",
            bufsize=1,  # 行缓冲
        )
    except Exception as e:
        elapsed = time.time() - start
        log(f"[SPAWN_FAIL] {name} - {e} ({elapsed:.0f}秒)")
        return {"name": name, "category": category, "status": "SPAWN_FAIL",
                "error": str(e), "elapsed": elapsed}

    last_output_time = time.time()
    try:
        # 实时转发子进程输出
        for line in proc.stdout:
            line = line.rstrip("\r\n")
            if line:
                print(f"    | {line}", flush=True)
                last_output_time = time.time()
                _heartbeat_state["last_child_output"] = datetime.now().isoformat()
            # 软超时检测
            if time.time() - start > TIMEOUT:
                log(f"  [TIMEOUT] {name} 运行超过 {TIMEOUT}s，kill")
                try:
                    proc.kill()
                except Exception:
                    pass
                proc.wait(timeout=30)
                elapsed = time.time() - start
                return {"name": name, "category": category, "status": "TIMEOUT",
                        "elapsed": elapsed}
        # 正常读完
        rc = proc.wait(timeout=60)
        elapsed = time.time() - start
        log(f"  [Popen] 子进程退出 rc={rc}，耗时 {elapsed:.0f}s")

        kimi_file = KIMI_OUTPUT_DIR / f"{name}_kimi.md"
        if kimi_file.exists():
            target_file = TARGET_DIR / category / f"{name}.md"
            target_file.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(kimi_file), str(target_file))
            line_count = len(target_file.read_text(encoding="utf-8", errors="replace").splitlines())
            log(f"[OK] {name} -> {category}/{name}.md ({line_count}行, {elapsed:.0f}秒)")
            return {"name": name, "category": category, "status": "OK",
                    "lines": line_count, "elapsed": elapsed}
        else:
            log(f"[FAIL] {name} - 文件未生成 ({elapsed:.0f}秒, rc={rc})")
            return {"name": name, "category": category, "status": "FAIL",
                    "returncode": rc, "elapsed": elapsed}

    except Exception as e:
        elapsed = time.time() - start
        log(f"[ERROR] {name} - {type(e).__name__}: {e} ({elapsed:.0f}秒)")
        try:
            proc.kill()
        except Exception:
            pass
        return {"name": name, "category": category, "status": "ERROR",
                "error": f"{type(e).__name__}: {e}", "elapsed": elapsed}


# ── 结果持久化（断点续跑支持）───────────────────────
def load_results() -> dict:
    if RESULTS_FILE.exists():
        try:
            return json.loads(RESULTS_FILE.read_text(encoding="utf-8"))
        except Exception:
            return {}
    return {}


def save_results(results: dict):
    try:
        RESULTS_FILE.write_text(json.dumps(results, ensure_ascii=False, indent=2),
                                encoding="utf-8")
    except Exception as e:
        log(f"  [warn] 写 results.json 失败: {e}")


# ── 主函数 ───────────────────────────────────────────
def main():
    parser = argparse.ArgumentParser(description="Kimi 批量搜集人物传记（鲁棒版）")
    parser.add_argument("--tag", default="", help="日志文件名附加标签（如 rerun）")
    parser.add_argument("--resume", action="store_true",
                        help="续跑：跳过 results.json 中 status=OK 的人物")
    parser.add_argument("--only", default=None,
                        help="只跑指定人物（逗号分隔，如 '张居正,海瑞'）")
    args = parser.parse_args()

    LOG_DIR.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    tag = f"_{args.tag}" if args.tag else ""
    log_path = LOG_DIR / f"batch_biography_p1{tag}_{timestamp}.log"
    log_file = open(log_path, "w", encoding="utf-8")
    sys.stdout = Tee(sys.__stdout__, log_file)
    sys.stderr = Tee(sys.__stderr__, log_file)

    # 启动心跳线程
    hb = threading.Thread(target=heartbeat_loop, name="batch-heartbeat", daemon=True)
    hb.start()
    update_status(phase="starting", extra={"log_path": str(log_path)})

    log(f"=== Kimi 批量搜集启动（鲁棒版）===")
    log(f"  日志文件: {log_path}")
    log(f"  状态文件: {STATUS_FILE}")
    log(f"  结果文件: {RESULTS_FILE}")
    log(f"  超时: {TIMEOUT} 秒/人")
    log(f"  PID: {os.getpid()}")
    log(f"  Python: {sys.executable}")
    log(f"  Research pipeline: {RESEARCH_PIPELINE}")

    # 筛选人物清单
    persons = PERSONS
    if args.only:
        only_set = set(s.strip() for s in args.only.split(",") if s.strip())
        persons = [p for p in persons if p[0] in only_set]
        log(f"  --only 过滤后: {len(persons)} 人")

    existing = load_results() if args.resume else {}
    if args.resume and existing:
        ok_names = [n for n, r in existing.items() if r.get("status") == "OK"]
        log(f"  --resume: results.json 中已成功 {len(ok_names)} 人，将跳过")

    start_all = time.time()

    for idx, (name, identity, category) in enumerate(persons, 1):
        # 检查目标文件已经是新产物（续跑时）
        if args.resume:
            prev = existing.get(name)
            if prev and prev.get("status") == "OK":
                log(f"--- [{idx}/{len(persons)}] {name} ({category}) SKIP（已成功）---")
                continue

        log(f"")
        log(f"--- [{idx}/{len(persons)}] {name} ({category}) ---")
        update_status(phase="running", current=name,
                      extra={"progress": f"{idx}/{len(persons)}"})
        result = run_person(name, identity, category)
        existing[name] = result
        save_results(existing)

    _heartbeat_stop.set()
    total_time = time.time() - start_all
    log(f"")
    log(f"{'='*60}")
    log(f"完成！总耗时: {total_time:.0f}秒 ({total_time/60:.1f}分钟)")
    ok = [r for r in existing.values() if r.get("status") == "OK"]
    log(f"成功: {len(ok)}/{len(existing)}")
    if ok:
        total_lines = sum(r.get("lines", 0) for r in ok)
        log(f"总行数: {total_lines}, 平均: {total_lines/len(ok):.0f}行/人")
    fail = [(r.get("name"), r.get("status")) for r in existing.values()
            if r.get("status") != "OK"]
    if fail:
        log(f"失败: {fail}")

    update_status(phase="done", current="",
                  extra={"total_elapsed": total_time,
                         "success_count": len(ok),
                         "fail_count": len(fail)})
    log_file.close()


if __name__ == "__main__":
    main()
