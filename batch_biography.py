import subprocess
import concurrent.futures
import os
import shutil
import time

BASE_DIR = "G:/AIDev/Ming_Simulation_Research"
KIMI_OUTPUT_DIR = os.path.join(BASE_DIR, "角色视角")
TARGET_DIR = os.path.join(BASE_DIR, "人物名录")

PERSONS = [
    ("朱载坖", "隆庆帝/明穆宗，明朝第十二位皇帝，批准隆庆开关和俺答封贡", "帝王"),
    ("朱翊钧", "万历帝/明神宗，明朝第十三位皇帝，在位48年，前期改革后期怠政", "帝王"),
    ("严嵩", "内阁首辅（1548-1562），嘉靖朝权臣，善写青词获宠", "阁臣重臣"),
    ("张居正", "内阁首辅（1572-1582），推行考成法、一条鞭法、清丈田亩", "阁臣重臣"),
    ("申时行", "内阁首辅（1583-1591），万历中期调和皇帝与群臣", "阁臣重臣"),
    ("李贽", "思想家，提出童心说，挑战程朱理学正统，入狱自刎", "文人学者"),
    ("利玛窦", "意大利耶稣会传教士(Matteo Ricci)，西学东渐核心人物", "外国人物"),
]

PROMPT_TEMPLATE = """研究明朝人物{name}（{identity}）的完整生平档案。聚焦其在1550-1600年间的活动，但完整覆盖其一生。

请按以下格式输出：

# {name}

## 基本信息
- 生卒：（精确到年月日，如可考）
- 字号：
- 籍贯：
- 身份/官职：（列出主要官职变迁）

## 生平概要
（800字以内的完整叙述，涵盖出身、成长、仕途/事业、晚年/结局）

## 关键事迹
（按时间顺序，每条标注来源，至少10条）
- 年份：事迹 [来源：xxx]

## 重要人际关系
- 上级/君主：
- 盟友/同党：
- 对手/政敌：
- 下属/门生：
- 家族：

## 历史评价
### 时人评价
（引用原文）
### 后世评价
（引用学术观点）

## 关联主题
- → [相关研究方向]：关联说明

## 存疑 & 待查
- 问题1
- 问题2

## 来源汇总
1. 来源名称 — 类型（古籍/论文/百科/专著）— 可信度评估
（古籍须注明卷次，如《明史·xxx传》卷xx）
"""

def run_person(name, identity, category):
    prompt = PROMPT_TEMPLATE.format(name=name, identity=identity)
    cmd = [
        "python", os.path.join(BASE_DIR, "research_pipeline.py"),
        name,
        "--kimi-only",
        "--prompt", prompt
    ]
    start = time.time()
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=1200, cwd=BASE_DIR)
        elapsed = time.time() - start

        # Check if output file exists
        kimi_file = os.path.join(KIMI_OUTPUT_DIR, f"{name}_kimi.md")
        if os.path.exists(kimi_file):
            # Move to target directory
            target_file = os.path.join(TARGET_DIR, category, f"{name}.md")
            shutil.move(kimi_file, target_file)
            line_count = len(open(target_file, encoding='utf-8').readlines())
            print(f"[OK] {name} -> {category}/{name}.md ({line_count}行, {elapsed:.0f}秒)")
            return (name, category, "OK", line_count, elapsed)
        else:
            print(f"[FAIL] {name} - 输出文件未生成 ({elapsed:.0f}秒)")
            print(f"  stdout: {result.stdout[-200:] if result.stdout else 'empty'}")
            print(f"  stderr: {result.stderr[-200:] if result.stderr else 'empty'}")
            return (name, category, "FAIL", 0, elapsed)
    except subprocess.TimeoutExpired:
        elapsed = time.time() - start
        print(f"[TIMEOUT] {name} ({elapsed:.0f}秒)")
        return (name, category, "TIMEOUT", 0, elapsed)
    except Exception as e:
        elapsed = time.time() - start
        print(f"[ERROR] {name} - {e} ({elapsed:.0f}秒)")
        return (name, category, "ERROR", 0, elapsed)

if __name__ == "__main__":
    print(f"开始批量搜集 {len(PERSONS)} 位人物传记...")
    print(f"并发数: 3")
    print()

    start_all = time.time()
    results = []

    with concurrent.futures.ThreadPoolExecutor(max_workers=3) as executor:
        futures = {executor.submit(run_person, n, i, c): n for n, i, c in PERSONS}
        for future in concurrent.futures.as_completed(futures):
            results.append(future.result())

    total_time = time.time() - start_all

    # Summary
    print()
    print("=" * 60)
    print(f"完成！总耗时: {total_time:.0f}秒 ({total_time/60:.1f}分钟)")
    ok = [r for r in results if r[2] == "OK"]
    fail = [r for r in results if r[2] != "OK"]
    print(f"成功: {len(ok)}/{len(results)}")
    if ok:
        total_lines = sum(r[3] for r in ok)
        print(f"总行数: {total_lines}")
    if fail:
        print(f"失败: {[r[0] for r in fail]}")
