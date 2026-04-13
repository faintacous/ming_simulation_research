"""
opencode + ming-dynasty-biographer agent 批量搜集 v4
- 使用受限 agent（禁用 question/task/read/glob 等工具）
- 2 并发（避免资源竞争）
"""
import subprocess
import concurrent.futures
import os
import time
import glob
import shutil

BASE_DIR = "G:/AIDev/Ming_Simulation_Research"
TARGET_DIR = os.path.join(BASE_DIR, "人物名录")
OPENCODE_BIN = "C:/Users/pc/AppData/Roaming/npm/opencode.cmd"
AGENT_NAME = "ming-dynasty-biographer"

PERSONS = [
    # 最后 2 人（之前因 glm-4.6 SSE 超时失败）
    ("李舜臣", "朝鲜水军将领，龟船战术，露梁海战殉国", "外国人物"),
    ("小西行长", "日军第一军团指挥官，朝鲜之役先锋", "外国人物"),
]

PROMPT_TEMPLATE = """搜集明代人物 {name}（{identity}）的完整生平档案。

【文件路径——绝对严格】
最终文件必须写入以下**完整绝对路径**，不得使用任何其他路径：
G:/AIDev/Ming_Simulation_Research/人物名录/{category}/{name}.md

硬约束：
- 文件名必须是 "{name}.md"，不要加任何后缀（不是 "{name}-完整生平档案.md" 或 "{name}_传记.md"）
- 不要创建新文件夹。不要使用 05-军事与边防/、角色视角/、人物传记/、人物档案/ 等其他路径
- 不要写入项目根目录
- 只使用 Write 工具写入上面给定的完整路径，一次性写入全部内容

【内容要求】
- 聚焦1550-1600年间的活动，但完整覆盖其一生
- 必须使用 WebFetch 联网搜集（维基百科中文、百度百科、维基文库、ctext.org 等）
- 至少 5 个独立来源、20 条关键事迹、5 条存疑
- 按 agent 系统提示中的标准模板输出
- 预期篇幅 300 行以上

直接开始搜集，然后调用 Write 工具写入上述绝对路径。不要询问任何问题。"""

def find_and_move_file(name, category):
    """模型常常把文件写到错误路径。这里扫描整个项目找包含该人物名字的最近 .md 文件，移动到正确位置。"""
    target_file = os.path.join(TARGET_DIR, category, f"{name}.md")
    if os.path.exists(target_file):
        return target_file

    candidates = []
    for root, dirs, files in os.walk(BASE_DIR):
        # 跳过备份和工具目录
        if "_backup" in root or "logs" in root or "角色视角" in root and "_kimi" in root:
            continue
        for fn in files:
            if fn.endswith(".md") and name in fn:
                full = os.path.join(root, fn)
                # 只看最近 30 分钟内修改的
                if time.time() - os.path.getmtime(full) < 1800:
                    candidates.append(full)

    if not candidates:
        return None

    # 取最大的那个（内容最多的）
    candidates.sort(key=lambda p: os.path.getsize(p), reverse=True)
    src = candidates[0]
    os.makedirs(os.path.dirname(target_file), exist_ok=True)
    shutil.move(src, target_file)
    return target_file


def run_person(name, identity, category):
    prompt = PROMPT_TEMPLATE.format(name=name, identity=identity, category=category)
    cmd = [
        OPENCODE_BIN, "run",
        "--agent", AGENT_NAME,
        "--dir", BASE_DIR,
        prompt
    ]
    start = time.time()

    try:
        result = subprocess.run(
            cmd, capture_output=True, text=True,
            timeout=1500, encoding='utf-8', errors='replace'
        )
        elapsed = time.time() - start

        # 不管模型写到哪，都尝试找到并移动到正确位置
        target_file = find_and_move_file(name, category)

        if target_file and os.path.exists(target_file):
            with open(target_file, encoding='utf-8') as f:
                content = f.read()
            line_count = len(content.splitlines())
            url_count = content.count('http')
            moved_hint = "" if os.path.basename(target_file) == f"{name}.md" else " [已搬运]"
            print(f"[OK] {name} ({category}) - {line_count}行, {url_count} URL, {elapsed:.0f}秒{moved_hint}", flush=True)
            return (name, category, "OK", line_count, url_count, elapsed)
        else:
            print(f"[FAIL] {name} - 找不到生成文件 ({elapsed:.0f}秒)", flush=True)
            return (name, category, "FAIL", 0, 0, elapsed)
    except subprocess.TimeoutExpired:
        elapsed = time.time() - start
        print(f"[TIMEOUT] {name} ({elapsed:.0f}秒)", flush=True)
        return (name, category, "TIMEOUT", 0, 0, elapsed)
    except Exception as e:
        elapsed = time.time() - start
        print(f"[ERROR] {name} - {e} ({elapsed:.0f}秒)", flush=True)
        return (name, category, "ERROR", 0, 0, elapsed)

if __name__ == "__main__":
    print(f"开始批量搜集 {len(PERSONS)} 位人物传记（opencode + ming-dynasty-biographer）...")
    print(f"并发数: 2")
    print(flush=True)

    start_all = time.time()
    results = []

    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as executor:
        futures = {executor.submit(run_person, n, i, c): n for n, i, c in PERSONS}
        for future in concurrent.futures.as_completed(futures):
            results.append(future.result())

    total_time = time.time() - start_all
    print(f"\n{'='*60}")
    print(f"完成！总耗时: {total_time:.0f}秒 ({total_time/60:.1f}分钟)")
    ok = [r for r in results if r[2] == "OK"]
    print(f"成功: {len(ok)}/{len(results)}")
    if ok:
        total_lines = sum(r[3] for r in ok)
        total_urls = sum(r[4] for r in ok)
        print(f"总行数: {total_lines}, 平均: {total_lines/len(ok):.0f}行/人, 平均URL: {total_urls/len(ok):.1f}")
    fail = [r for r in results if r[2] != "OK"]
    if fail:
        print(f"失败: {[r[0] for r in fail]}")
