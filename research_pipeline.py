#!/usr/bin/env python3
"""
Ming Simulation Research Pipeline
==================================
三段流水线：Kimi（初稿）+ Codex（校验）→ 供 Opus 合并

用法：
    python research_pipeline.py 僧侣
    python research_pipeline.py 僧侣 --prompt "自定义研究提示"
    python research_pipeline.py 僧侣 --kimi-only
    python research_pipeline.py 僧侣 --codex-only
"""

import subprocess
import sys
import os
import re
import argparse
import time
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed

# ── 配置 ──────────────────────────────────────────────
OUTPUT_DIR = Path("G:/AIDev/ming_simulation_research/角色视角")
CODEX_TMP = Path("E:/System_Temp/codex_pipeline_out.txt")
KIMI_TMP = Path("E:/System_Temp/kimi_pipeline_out.txt")

# Windows 下需要完整路径（subprocess 不走 shell PATH）
CODEX_BIN = os.environ.get("CODEX_BIN", "C:/Users/pc/AppData/Roaming/npm/codex.cmd")
KIMI_BIN = os.environ.get("KIMI_BIN", "C:/Users/pc/.local/bin/kimi")

# ── Prompt 模板 ──────────────────────────────────────
PROMPT_TEMPLATE = """研究明朝{topic}的职业生命周期，聚焦嘉靖至崇祯（1521-1644）。

要求：
1. 以个体的完整职业经历为主线，从"如何成为{topic}"到"最终结局"
2. 每个阶段都要有：制度性规定 + 具体数据 + 真实案例
3. 必须包含以下维度（根据角色适当调整）：
   - 入行/入职途径（来源、条件、流程）
   - 日常生活与工作（作息、职责、居住、饮食）
   - 收入与经济状况（正式收入、灰色收入、生活水平）
   - 晋升/发展路径
   - 风险与困境
   - 退出/结局
4. 标注所有来源（古籍需注明卷次）
5. 用简体中文撰写"""


def build_prompt(topic: str, custom_prompt: str | None = None) -> str:
    if custom_prompt:
        return custom_prompt
    return PROMPT_TEMPLATE.format(topic=topic)


# ── Kimi 调用 ────────────────────────────────────────
def run_kimi(topic: str, prompt: str) -> dict:
    """在空临时目录运行 Kimi，提取生成的文件内容"""
    import tempfile

    work_dir = tempfile.mkdtemp(prefix="kimi_research_")
    kimi_prompt = prompt + f"\n\n将研究报告写入当前目录的 {topic}.md 文件，400-700行。"

    env = os.environ.copy()
    env["PYTHONUTF8"] = "1"
    env["PYTHONIOENCODING"] = "utf-8"

    print(f"  [Kimi] 启动研究：{topic}")
    start = time.time()

    try:
        result = subprocess.run(
            [
                KIMI_BIN, "--print", "--max-steps-per-turn", "30",
                "-p", kimi_prompt,
            ],
            cwd=work_dir,
            capture_output=True,
            text=True,
            env=env,
            timeout=1800,
            encoding="utf-8",
            errors="replace",
        )

        elapsed = time.time() - start

        # 查找 Kimi 写入的文件
        output_file = Path(work_dir) / f"{topic}.md"
        if not output_file.exists():
            # 尝试查找任何 .md 文件
            md_files = list(Path(work_dir).glob("*.md"))
            if md_files:
                output_file = md_files[0]

        if output_file.exists():
            content = output_file.read_text(encoding="utf-8", errors="replace")
            lines = len(content.splitlines())
            print(f"  [Kimi] 完成！{lines} 行，耗时 {elapsed:.0f}s")
            return {
                "success": True,
                "content": content,
                "lines": lines,
                "elapsed": elapsed,
                "source": "kimi",
            }
        else:
            # Kimi 没有写文件，尝试从 stdout 提取
            print(f"  [Kimi] 未找到输出文件，尝试从 stdout 提取...")
            # 保存原始输出供调试
            raw_path = KIMI_TMP
            raw_path.write_text(result.stdout or "", encoding="utf-8")

            # 提取 WriteFile 中的内容
            match = re.search(
                r'"content":\s*"(.*?)"(?=\s*\})',
                result.stdout or "",
                re.DOTALL,
            )
            if match:
                content = match.group(1)
                content = content.replace("\\n", "\n").replace('\\"', '"')
                lines = len(content.splitlines())
                print(f"  [Kimi] 从 stdout 提取成功！{lines} 行，耗时 {elapsed:.0f}s")
                return {
                    "success": True,
                    "content": content,
                    "lines": lines,
                    "elapsed": elapsed,
                    "source": "kimi",
                }

            print(f"  [Kimi] 失败：未找到输出")
            return {
                "success": False,
                "error": "No output file or extractable content",
                "elapsed": elapsed,
                "source": "kimi",
                "raw_output_path": str(raw_path),
            }

    except subprocess.TimeoutExpired:
        elapsed = time.time() - start
        print(f"  [Kimi] 超时（{elapsed:.0f}s）")
        return {"success": False, "error": "Timeout", "elapsed": elapsed, "source": "kimi"}
    except Exception as e:
        elapsed = time.time() - start
        print(f"  [Kimi] 异常：{e}")
        return {"success": False, "error": str(e), "elapsed": elapsed, "source": "kimi"}


# ── Codex 调用 ───────────────────────────────────────
def run_codex(topic: str, prompt: str) -> dict:
    """运行 Codex，输出到临时文件"""
    codex_prompt = prompt + "\n\n给出详细研究结果，重点引用一手古籍（如《大明会典》《大明律》《明史》）的具体卷次和条文。对无法确认的数据明确标注'存疑'。"
    out_path = str(CODEX_TMP)

    print(f"  [Codex] 启动研究：{topic}")
    start = time.time()

    try:
        result = subprocess.run(
            [
                CODEX_BIN, "exec",
                "--skip-git-repo-check",
                "--sandbox", "read-only",
                "--ephemeral",
                "-o", out_path,
                codex_prompt,
            ],
            capture_output=True,
            text=True,
            timeout=1800,
            encoding="utf-8",
            errors="replace",
        )

        elapsed = time.time() - start
        out_file = Path(out_path)

        if out_file.exists():
            content = out_file.read_text(encoding="utf-8", errors="replace")
            lines = len(content.splitlines())
            print(f"  [Codex] 完成！{lines} 行，耗时 {elapsed:.0f}s")
            return {
                "success": True,
                "content": content,
                "lines": lines,
                "elapsed": elapsed,
                "source": "codex",
            }
        else:
            stderr = result.stderr or ""
            print(f"  [Codex] 失败：{stderr[:200]}")
            return {
                "success": False,
                "error": stderr[:500],
                "elapsed": elapsed,
                "source": "codex",
            }

    except subprocess.TimeoutExpired:
        elapsed = time.time() - start
        print(f"  [Codex] 超时（{elapsed:.0f}s）")
        return {"success": False, "error": "Timeout", "elapsed": elapsed, "source": "codex"}
    except Exception as e:
        elapsed = time.time() - start
        print(f"  [Codex] 异常：{e}")
        return {"success": False, "error": str(e), "elapsed": elapsed, "source": "codex"}


# ── 保存结果 ─────────────────────────────────────────
def save_result(topic: str, result: dict) -> Path | None:
    if not result["success"]:
        return None

    source = result["source"]
    filename = f"{topic}_{source}.md"
    filepath = OUTPUT_DIR / filename
    filepath.write_text(result["content"], encoding="utf-8")
    return filepath


# ── 对比摘要 ─────────────────────────────────────────
def print_comparison(topic: str, results: list[dict]):
    print("\n" + "=" * 60)
    print(f"  研究主题：{topic}")
    print("=" * 60)

    for r in results:
        src = r["source"].upper()
        if r["success"]:
            print(f"  [{src}] {r['lines']} 行 | {r['elapsed']:.0f}s")
        else:
            print(f"  [{src}] 失败：{r.get('error', 'unknown')[:80]}")

    # 统计两者都有的情况
    successes = [r for r in results if r["success"]]
    if len(successes) == 2:
        print("\n  ── 对比要点 ──")
        kimi_r = next((r for r in successes if r["source"] == "kimi"), None)
        codex_r = next((r for r in successes if r["source"] == "codex"), None)
        if kimi_r and codex_r:
            ratio = kimi_r["lines"] / max(codex_r["lines"], 1)
            print(f"  篇幅比：Kimi {kimi_r['lines']}行 / Codex {codex_r['lines']}行 = {ratio:.1f}x")
            print(f"  速度比：Kimi {kimi_r['elapsed']:.0f}s / Codex {codex_r['elapsed']:.0f}s")

    print("\n  ── 输出文件 ──")
    for r in results:
        if r["success"]:
            src = r["source"]
            print(f"  {OUTPUT_DIR / f'{topic}_{src}.md'}")

    print(f"\n  下一步：让 Opus 读取以上文件，合并为 {OUTPUT_DIR / f'{topic}.md'}")
    print("=" * 60)


# ── 主函数 ───────────────────────────────────────────
def main():
    parser = argparse.ArgumentParser(
        description="Ming Simulation Research Pipeline: Kimi + Codex → Opus"
    )
    parser.add_argument("topic", help="研究角色名称（如：僧侣、道士、盐商）")
    parser.add_argument("--prompt", help="自定义研究提示（覆盖默认模板）", default=None)
    parser.add_argument("--kimi-only", action="store_true", help="只运行 Kimi")
    parser.add_argument("--codex-only", action="store_true", help="只运行 Codex")
    args = parser.parse_args()

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    prompt = build_prompt(args.topic, args.prompt)

    print(f"\n{'=' * 60}")
    print(f"  Ming Research Pipeline")
    print(f"  主题：{args.topic}")
    print(f"{'=' * 60}\n")

    runners = []
    if not args.codex_only:
        runners.append(("kimi", run_kimi))
    if not args.kimi_only:
        runners.append(("codex", run_codex))

    results = []
    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = {
            executor.submit(fn, args.topic, prompt): name
            for name, fn in runners
        }
        for future in as_completed(futures):
            name = futures[future]
            try:
                result = future.result()
                results.append(result)
                save_result(args.topic, result)
            except Exception as e:
                print(f"  [{name.upper()}] 未捕获异常：{e}")
                results.append({
                    "success": False,
                    "error": str(e),
                    "elapsed": 0,
                    "source": name,
                })

    print_comparison(args.topic, results)


if __name__ == "__main__":
    main()
