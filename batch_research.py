#!/usr/bin/env python3
"""
批量调度 research_pipeline.py，控制并发上限。

用法：
    python batch_research.py              # 默认并发 3
    python batch_research.py --workers 2  # 并发 2
    python batch_research.py --dry-run    # 只列出待处理列表
"""

import subprocess
import sys
import os
import time
import argparse
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed

OUTPUT_DIR = Path("G:/AIDev/ming_simulation_research/角色视角")
PIPELINE = Path("G:/AIDev/ming_simulation_research/research_pipeline.py")

# ── 全部 45 个角色 ──────────────────────────────────
ALL_ROLES = [
    # 宫廷
    "皇帝", "后妃", "宫女", "宦官", "藩王与宗室",
    # 文官体系
    "科举士子", "京官", "言官", "地方官知县", "吏员胥吏", "幕僚师爷",
    # 军事
    "武将", "锦衣卫", "卫所军士", "募兵营兵",
    # 商业
    "内陆商人", "海商走私商", "盐商",
    # 农业
    "自耕农", "佃农", "地方地主",
    # 手工业与劳动
    "手工匠人", "矿工灶户", "船夫漕军", "驿卒",
    # 社会底层
    "流民", "奴仆", "娼妓与乐籍女性",
    # 女性
    "妇女",
    # 宗教
    "僧侣", "道士", "传教士",
    # 文化艺术
    "戏曲艺人", "医生", "丹青画匠",
    # 其他职业
    "讼师", "相命术士", "媒婆", "吹鼓手乐户", "巫与大神",
    "镖师走卒", "起义者",
    # 补充
    "乡绅里长", "书坊主与刻工", "牙行经纪",
]


def is_done(role: str) -> bool:
    """检查角色是否已有终稿或 kimi 草稿"""
    final_file = OUTPUT_DIR / f"{role}.md"
    kimi_file = OUTPUT_DIR / f"{role}_kimi.md"
    return final_file.exists() or kimi_file.exists()


def get_pending_roles() -> list[str]:
    return [r for r in ALL_ROLES if not is_done(r)]


def run_one(role: str, index: int, total: int) -> dict:
    """运行单个角色的 pipeline"""
    start = time.time()
    print(f"  [{index}/{total}] 开始：{role}")

    env = os.environ.copy()
    env["PYTHONUTF8"] = "1"

    try:
        result = subprocess.run(
            [sys.executable, str(PIPELINE), role],
            capture_output=True,
            text=True,
            env=env,
            timeout=900,  # 15 分钟超时
            encoding="utf-8",
            errors="replace",
        )
        elapsed = time.time() - start

        # 检查输出文件
        kimi_file = OUTPUT_DIR / f"{role}_kimi.md"
        codex_file = OUTPUT_DIR / f"{role}_codex.md"
        kimi_lines = len(kimi_file.read_text(encoding="utf-8").splitlines()) if kimi_file.exists() else 0
        codex_lines = len(codex_file.read_text(encoding="utf-8").splitlines()) if codex_file.exists() else 0

        status = "OK" if kimi_lines > 0 else "FAIL"
        print(f"  [{index}/{total}] {status}：{role} | Kimi {kimi_lines}行 Codex {codex_lines}行 | {elapsed:.0f}s")

        return {
            "role": role,
            "success": kimi_lines > 0,
            "kimi_lines": kimi_lines,
            "codex_lines": codex_lines,
            "elapsed": elapsed,
        }

    except subprocess.TimeoutExpired:
        elapsed = time.time() - start
        print(f"  [{index}/{total}] 超时：{role} | {elapsed:.0f}s")
        return {"role": role, "success": False, "kimi_lines": 0, "codex_lines": 0, "elapsed": elapsed}
    except Exception as e:
        elapsed = time.time() - start
        print(f"  [{index}/{total}] 异常：{role} | {e}")
        return {"role": role, "success": False, "kimi_lines": 0, "codex_lines": 0, "elapsed": elapsed}


def main():
    parser = argparse.ArgumentParser(description="批量运行研究流水线")
    parser.add_argument("--workers", type=int, default=3, help="并发数（默认 3）")
    parser.add_argument("--dry-run", action="store_true", help="只列出待处理列表")
    parser.add_argument("--roles", nargs="*", help="指定要研究的角色（覆盖自动检测）")
    args = parser.parse_args()

    if args.roles:
        pending = args.roles
    else:
        pending = get_pending_roles()

    print(f"\n{'=' * 60}")
    print(f"  Ming Research Batch Runner")
    print(f"  待处理：{len(pending)} 个角色 | 并发：{args.workers}")
    print(f"{'=' * 60}")

    if args.dry_run:
        for i, role in enumerate(pending, 1):
            print(f"  {i:2d}. {role}")
        print(f"\n  共 {len(pending)} 个角色待研究")
        return

    print()
    results = []
    total = len(pending)
    start_all = time.time()

    with ThreadPoolExecutor(max_workers=args.workers) as executor:
        futures = {
            executor.submit(run_one, role, i, total): role
            for i, role in enumerate(pending, 1)
        }
        for future in as_completed(futures):
            try:
                result = future.result()
                results.append(result)
            except Exception as e:
                role = futures[future]
                print(f"  未捕获异常：{role} | {e}")
                results.append({"role": role, "success": False, "kimi_lines": 0, "codex_lines": 0, "elapsed": 0})

    total_elapsed = time.time() - start_all

    # ── 汇总报告 ──
    print(f"\n{'=' * 60}")
    print(f"  批量研究完成")
    print(f"{'=' * 60}")

    successes = [r for r in results if r["success"]]
    failures = [r for r in results if not r["success"]]

    print(f"\n  成功：{len(successes)} / {len(results)}")
    print(f"  失败：{len(failures)} / {len(results)}")
    print(f"  总耗时：{total_elapsed:.0f}s ({total_elapsed/60:.1f}min)")

    if successes:
        total_kimi = sum(r["kimi_lines"] for r in successes)
        total_codex = sum(r["codex_lines"] for r in successes)
        print(f"  Kimi 总行数：{total_kimi}")
        print(f"  Codex 总行数：{total_codex}")

    if failures:
        print(f"\n  ── 失败列表 ──")
        for r in failures:
            print(f"  - {r['role']}")

    print(f"\n{'=' * 60}")


if __name__ == "__main__":
    main()
