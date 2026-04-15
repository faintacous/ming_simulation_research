# 人物传记校验流水线 — 设计与实现记录

> 本文档是给未来的 Claude 和代理读的工程记录，不是给人类读者看的教程。
> 目的：把决策、约束、衔接协议固化下来，让下次跑 P2/P3 批次时能直接看懂架构。

## 1. 设计目标

把 Kimi 产出的人物传记初稿（P1 批次 13 人）提升为「尽可能详细的史实资料」。
"详细"在这里有具体、可验收的定义：

- 通过 CLAUDE.md 中定义的"工作流 B：新传记校验（完整版）"全链路
- 通过 `人物名录/_validation/` 下 BIO-001 / BIO-003 / BIO-006 三项机器规则
- 每篇传记都附带一份 Codex 联网核对记录（锚定到 wikisource/ctext/CBDB 原文）
- 批次内同事件的跨文件叙述达成一致（BIO-005）

核心设计原则：**按 CLAUDE.md 确立的 Kimi + Codex 分工**——Kimi 负责生成，
Codex 负责校验，不混用。

## 2. 流水线全景

```
[Kimi 初稿]  ← tools/batch_biography.py 已产出（batch_biography_p1_20260415_130707.log）
     │
     ├── 阶段 A: Codex 综合校验（单文件诊断）
     │        输入: 人物名录/{分类}/{人物}.md
     │        输出: _validation/P1_批次_YYYYMMDD/{人物}_工单.md
     │        超时: 600s/人
     │
     ├── 阶段 B: 按工单修订（Codex 执行精确修订）
     │        输入: {人物}.md + {人物}_工单.md
     │        输出: {人物}.md（原地覆盖）+ {人物}_修订记录.md
     │        超时: 900s/人
     │        策略说明见 §5
     │
     ├── 阶段 C: Codex 联网核对
     │        输入: 修订后的 {人物}.md
     │        输出: _validation/P1_批次_YYYYMMDD/{人物}_联网核对.md
     │        超时: 900s/人
     │        约束: prompt 必须显式要求"以实际打开的页面为准"（见 §6）
     │
     ├── 阶段 D: 跨文件一致性检查 (BIO-005)
     │        输入: 修订后的 13 份全体
     │        输出: _validation/P1_批次_YYYYMMDD/BIO-005_交叉一致性报告.md
     │        超时: 1800s（整批一次调用）
     │
     ├── 阶段 E: BIO-006 机器审计
     │        调用 人物名录/_validation/tools/run_validation.py --all
     │        输出: 三份标准报告（BIO-001/003/006）落回 _validation/ 根目录
     │
     └── 阶段 F: 汇总报告
              输出: _validation/P1_批次_YYYYMMDD/综合验收报告.md
```

## 3. 衔接协议

每个阶段的"产物-落盘位置-下游读取方式"都固定下来，阶段之间靠文件通信，
不传内存对象。这样可以支持中断重启和幂等性。

| 阶段 | 产物 | 落盘位置 | 下游读取 |
|------|------|----------|----------|
| Kimi 初稿 | `{人物}.md` | `人物名录/{分类}/{人物}.md` | A 读取 |
| A 综合校验 | `{人物}_工单.md` | `_validation/P1_批次_{ts}/` | B 读取 |
| B 修订 | 原地覆盖 + `{人物}_修订记录.md` | 同上目录 | C/D 读取 |
| C 联网核对 | `{人物}_联网核对.md` | 同上目录 | F 汇总 |
| D 跨文件 | `BIO-005_交叉一致性报告.md` | 同上目录 | F 汇总 |
| E 机器审计 | `BIO-00X*.md/json` | `_validation/` 根目录（保持与历史一致） | F 汇总 |
| F 汇总 | `综合验收报告.md` | 同 P1 批次目录 | 终点 |

**批次目录命名**：`P1_批次_YYYYMMDD`（如 `P1_批次_20260415`），从脚本启动日期取。
P2/P3 复用时命名为 `P2_批次_YYYYMMDD` / `P3_批次_YYYYMMDD`。

## 4. 幂等性设计

脚本支持中断重启，已完成的阶段跳过。判断方法：

- 阶段 A：检查 `{人物}_工单.md` 是否存在且非空
- 阶段 B：检查 `{人物}_修订记录.md` 是否存在
- 阶段 C：检查 `{人物}_联网核对.md` 是否存在且非空
- 阶段 D：检查 `BIO-005_交叉一致性报告.md` 是否存在
- 阶段 E：总是重跑（机器审计本身幂等、且成本低）
- 阶段 F：总是重写（汇总报告依赖上游所有产物）

强制重跑：删除对应产物文件，或传 `--force-stage A/B/C/D` 标志。

## 5. 阶段 B 修订策略选型

有三条路径可选：

1. **Kimi 重写**：把工单喂给 Kimi，让它在原 draft 基础上按工单修订
2. **Codex 精修**：把工单喂给 Codex，Codex 基于已有文件做定点修订
3. **人工审阅**：工单生成后暂停，人工处理

**选型：方案 2（Codex 精修）**。理由：

- CLAUDE.md "工作流 C" 已明确规定：Codex 先诊断 + Agent 按诊断单修订 + Codex 复验
- Kimi 的长处是"从零到 500 行长稿"，不是"按清单改 20 处"，让它重写容易把无关内容也改动
- Codex 以"打开文件 → 基于文件内容作答"为默认行为模式，精确定位修订点是它的强项
- Codex 没有配额上限（Kimi 有 17-18 次/日的配额）
- 阶段 B 和阶段 C 都是 Codex，可以串联，减少上下文切换成本

**Codex 修订 prompt 的核心约束**：
- 显式要求"只修订工单中列出的问题，不要改动其他内容"
- 显式要求"基本信息中的争议性内容降级为存疑，不要在基本信息里下定论"
- 修订后必须在 `{人物}_修订记录.md` 里逐条说明"采纳/未采纳/部分采纳"及理由

## 6. 阶段 C 联网核对的 prompt 约束

这是整条流水线最容易出错的环节。历史教训（来自 `_validation/联网核对报告.md`）：

Codex 如果没有被显式约束，会"按训练记忆补空"——写出一些看似合理但实际上
不在它打开的页面里的内容。必须在 prompt 里加入：

- "以你实际打开的 wikisource/ctext/CBDB 页面为准"
- "不按训练记忆补空"
- "逐条标注原文 URL"
- "无法从原文钉死的，必须写'未证'或'待考'，不能写成定论"

核心工具：`WebFetch`，必须在 Codex 的可用工具里保持启用。

站点白名单（已在 `_validation/联网核对报告.md` 中验证可访问）：
- `zh.wikisource.org/wiki/明史/卷XXX`
- `zh.wikisource.org/wiki/明史紀事本末/卷XXX`
- `ctext.org/datawiki.pl`
- `cbdb.fas.harvard.edu/cbdbapi/person.php`

## 7. 阶段 D 跨文件一致性检查

P1 批次里已知的交叉热点（来自 `_validation/交叉一致性报告.md`）：

1. **李成梁 ↔ 努尔哈赤**：收养说、1583 年古勒寨之战父祖死亡年份
2. **国本之争**：张居正 / 朱翊钧 / 王喜姐 / 郑贵妃 / 李太后 五视角叙述
3. **朝鲜之役**：麻贵 / 谭纶 / 李成梁 三武将视角
4. **高拱被逐**："十岁太子"戏剧化引语在多篇间标准不一
5. **梃击案**：郑贵妃视角与其他条目的证据边界差异

阶段 D 的 Codex prompt 必须显式列出这些热点，要求逐一检查。

## 8. 失败兜底

每个阶段的失败处理策略不同：

| 阶段 | 单次失败 | 兜底动作 |
|------|----------|----------|
| A 综合校验 | 超时/返回空 | 重试一次；再失败则写 `{人物}_失败记录.md`，跳过但不阻塞其他人 |
| B 修订 | 超时/Codex 未产出文件 | 重试一次；失败则保留原稿，写失败记录 |
| C 联网核对 | WebFetch 不可用 | 重试一次；失败则写失败记录，不影响其他人 |
| D 跨文件 | 超时（1800s） | 不重试，写失败记录，进入阶段 E |
| E 机器审计 | 返回非零 | 不影响流水线推进，汇总报告里标注 |
| F 汇总 | 脚本自身 bug | 抛异常，保留所有上游产物 |

**失败记录文件格式**：`_validation/P1_批次_YYYYMMDD/{人物}_失败记录.md`，含时间戳、阶段、错误摘要、stderr tail。

## 9. 未来 P2/P3 复用的参数化

脚本支持命令行参数 `--batch-id P2 --persons-file <path>`：

- `--batch-id`：批次标识，决定产物目录名
- `--persons-file`：JSON 文件，格式 `[{"name": "...", "category": "..."}]`。默认读 P1 硬编码列表
- `--skip-wait`：跳过等待 Kimi 批次完成
- `--wait-shell <id>`：指定要等待的后台 shell ID（默认 `bz0pi2fow`）
- `--wait-log <path>`：指定要监控的日志路径
- `--force-stage <A/B/C/D>`：强制重跑某阶段（忽略幂等性）

扩展 P2 时只需：
1. 生成 P2 人物清单 JSON
2. 运行 `python tools/validate_biographies.py --batch-id P2 --persons-file p2.json --skip-wait`

## 10. 日志

每阶段独立日志，写到 `logs/validate_biographies_{batch_id}_{stage}_{ts}.log`：

- `validate_biographies_P1_A_xxx.log` — 阶段 A 全量
- `validate_biographies_P1_B_xxx.log` — 阶段 B 全量
- 以此类推

主控日志 `validate_biographies_P1_main_{ts}.log` 汇总所有阶段的进度、耗时、状态。

## 11. Codex 调用方式

复用 `tools/research_pipeline.py` 的 `run_codex` 模式：

```python
subprocess.run([
    CODEX_BIN, "exec",
    "--skip-git-repo-check",
    "--sandbox", "read-only",  # 阶段 A/C/D 用 read-only
    "--sandbox", "workspace-write",  # 阶段 B 需要写入，用 workspace-write
    "--ephemeral",
    "-o", out_path,
    prompt,
], capture_output=True, text=True, timeout=TIMEOUT, encoding="utf-8", errors="replace")
```

路径：`CODEX_BIN = "C:/Users/pc/AppData/Roaming/npm/codex.cmd"`（从 research_pipeline.py 确认）

**串行约束**：按项目最新偏好，Codex 调用必须串行，不能并行。这意味着 13 人的 A 阶段至少需要 `13 × 600s = 130 分钟`，总流水线串行时长约 6-8 小时。这是可接受的——质量优先于速度。

## 12. 等待上游任务完成

阻塞策略：

1. 每 5 分钟读取日志 `batch_biography_p1_20260415_130707.log` 的末尾 50 行
2. 检测完成标志：
   - 最后一行包含 `"完成！总耗时"` —— 见 `tools/batch_biography.py:181`
   - 或包含 `"[OK] 顾宪成"` / `"[FAIL] 顾宪成"` —— 最后一人结束标志（见 PERSONS 列表最后一项）
3. 检测到标志后等 30 秒，开始阶段 A

超时保护：如果 10 小时（36000s）仍未检测到完成标志，写警告进主日志，继续执行（假定已完成）。

## 13. 已知不做的事

- 不对每篇传记做 Kimi 二次重写（Kimi 配额有限，留给未来 P2 批次）
- 不对 BIO-006 不达标的文件自动回炉到 Kimi（成本太高，改由人工决策）
- 不 git commit（按用户要求）

## 14. 相关文件索引

- 主脚本：`tools/validate_biographies.py`
- 上游批次脚本：`tools/batch_biography.py`（不动）
- Codex/Kimi 调用封装：`tools/research_pipeline.py`（复用 run_codex 的命令格式）
- 机器审计工具：`人物名录/_validation/tools/run_validation.py`
- 流程规范：`人物名录/_validation/新传记验收流程.md`
- 历史参考：
  - `人物名录/_validation/综合校验报告.md`（综合校验输出风格）
  - `人物名录/_validation/联网核对报告.md`（联网核对输出风格）
  - `人物名录/_validation/交叉一致性报告.md`（BIO-005 输出风格）
  - `人物名录/_validation/全库工单.md`（工单结构）

---

## 15. 2026-04-15 首次启动失败排查

### 15.1 现象

2026-04-15 13:07 启动 `tools/batch_biography.py` P1 批次（13 人），后台 shell `bz0pi2fow`。**观测到的失败现象**：

- 日志 `logs/batch_biography_p1_20260415_130707.log` 仅 411 字节，停在
  `--- [1/13] 张居正 (阁臣重臣) ---`，此后再无更新
- 日志中**从未出现** `[Kimi] 启动研究：张居正` —— 说明 `research_pipeline.run_kimi` 里的第一个 print（紧跟 `subprocess.run` 之前）都没被父进程回显
- `tasklist` 无 python.exe 残留，Kimi 临时目录 `E:/System_Temp/kimi_research_v6rp3ujg/` 13:07 创建后仍为空
- 目标文件 `人物名录/阁臣重臣/张居正.md` mtime 仍是 2026-04-12，未被覆盖
- 校验流水线 `bz0pi2fow` 每 5 分钟轮询死日志，永远等不到"完成！总耗时"

### 15.2 根因分析

**Python 父进程被 Windows/harness 层面静默终止**，原因组合：

1. **`subprocess.run(capture_output=True)` 的致命缺陷**：Kimi CLI 对一次人物研究会输出数 MB 的思考过程到 stdout（WebFetch 工具日志、推理轨迹等）。`capture_output=True` 会把全部输出读入内存，Windows 平台上遇到长 stdout 时存在已知的 pipe 缓冲/内存暴涨风险，父进程可能在 Kimi 真正写完输出前就被 OS 或 harness 的 idle watcher 终止。
2. **父进程与 subprocess 之间没有心跳回显**：`subprocess.run` 是阻塞调用，Kimi 工作的 1000+ 秒里父进程不产生任何输出，外界（包括 claude code shell 监控、Windows Job Object 超时等）无法判断父进程是否活着。
3. **Tee 的 stdout/stderr 编码冲突**：日志文件以 UTF-8 打开，但 Python 3.13 Windows 默认控制台流是 GBK（代码页 936）。`sys.__stdout__.write(utf8_str)` 在 GBK 控制台上写中文会触发 UnicodeEncodeError，虽然 Tee 里有 `except Exception: pass`，但历史日志（`kimi_rerun_9`、`kimi_serial_7`）全是 GBK 乱码印证了这一点。本次日志首行是正常 UTF-8、第二行起是 GBK 乱码说明两种编码混写，这本身不是崩溃主因，但增加了诊断难度。
4. **devlog 04-14 已记录同类问题**：P4 市镇研究时 Kimi 完成了文件写入，但 pipeline Python 父进程提前退出，文件只在 `/e/System_Temp/kimi_research_c21p96mt/` 临时目录 —— 同样的 silent death 模式。当时解法是"手动搬运而非重跑"，没有根治父进程存活问题。

### 15.3 修复措施（tools/batch_biography.py 鲁棒化重写）

| 修复项 | 具体做法 |
|--------|---------|
| 强制 UTF-8 输出 | 脚本入口 `sys.stdout.reconfigure(encoding="utf-8", errors="replace")`，同时设置 `PYTHONUTF8=1 PYTHONIOENCODING=utf-8` |
| 替换 `subprocess.run` 为 `subprocess.Popen` + 实时流式读取 | `Popen(stdout=PIPE, stderr=STDOUT, bufsize=1)`，`for line in proc.stdout: print("    | "+line)`，避免大输出缓冲死锁 |
| 心跳状态文件 | `tools/batch_biography.status`（JSON），后台线程每 30s 更新 `last_update`，每行子进程输出也更新 `last_child_output`。外界通过读这个文件判断进程是否活着 |
| 结果持久化 + 断点续跑 | `tools/batch_biography.results.json` 每完成 1 人落盘，`--resume` 跳过 `status=OK` 的人物 |
| 绝对路径 | `BASE_DIR = Path("G:/AIDev/Ming_Simulation_Research")`，`RESEARCH_PIPELINE = TOOLS_DIR / "research_pipeline.py"`，避免 cwd 误配 |
| 子进程也强制无缓冲 | `cmd = [sys.executable, "-u", ...]`，`env["PYTHONUNBUFFERED"]="1"` |
| 软超时 | 进入读循环后每次检查 `time.time() - start > TIMEOUT`，超限主动 `proc.kill()`，不再依赖 `subprocess.TimeoutExpired` |
| 所有 print flush=True | log() 封装统一带 flush；Tee.write 写后即 flush |

### 15.4 重启

- 新 Kimi 批次：后台 shell `b81755exs`，日志 `logs/batch_biography_p1_rerun_20260415_152316.log`，PID 37944
- 新校验流水线：后台 shell `bz59jpbou`，日志 `logs/validate_biographies_P1_main_20260415_152406.log`，`--wait-log` 指向新 Kimi 日志
- 启动后 30 秒验证：status 文件心跳正常更新，日志中看到 `[Kimi] 启动研究：张居正`（修复前从未到达的位置）

### 15.5 未来避坑清单

1. 任何长时间 subprocess 调用都不要用 `subprocess.run(capture_output=True)` —— 改用 `Popen` + 流式读
2. 任何后台 Python 脚本都要写心跳文件到磁盘，不能只依赖日志（日志可能因编码/buffer 问题滞后）
3. Windows 下 Python 脚本启动时第一行就必须 `sys.stdout.reconfigure(encoding="utf-8")`
4. 多小时运行的脚本要持久化中间结果到 JSON，支持断点续跑
5. 上下游流水线解耦要做到"下游能看到上游正在进行"—— `batch_biography.status` 即是给 validate_biographies 的心跳通道（当前还没用上，可在下次迭代中让 validate 读这个文件而不是读日志末尾字符串）

---

## 16. 问题 D 深度诊断：`run_in_background` + subprocess 的 2 分钟死亡（2026-04-15 补记）

### 16.1 第 15 节修复被证伪

第 15.3 节的鲁棒化（Popen 流式读 + UTF-8 + 心跳文件 + 软超时 + 断点续跑）**未解决根本问题**。第二次重启（15:23:16，后台 shell `b81755exs`，PID 37944）：

- 心跳 `last_update` 停在 15:25:16 — **启动后正好 2 分钟**
- 心跳停滞时间点与第一次失败（13:07 启动，心跳没能刷第二次）高度一致
- `tasklist` 无 PID 37944，`batch_biography.results.json` 从未创建
- 日志停在 `[Kimi] 启动研究：张居正` — 与第一次相比多走了一步（Popen 的 stdout 转发生效），但进程本体仍被杀

结论：死因**不在 Python subprocess 语义**，在更外层的生命周期管理。

### 16.2 新的对照数据（当日实测）

| 场景 | 生存时间 | 结果 | 备注 |
|------|---------|------|------|
| `run_in_background=true` + python + subprocess(kimi) | ~2 min | 死 | batch_biography.py 第一次/第二次 |
| Agent 前台 bash 直接调 kimi CLI | 7–13 min 完整跑完 | 成功 | 张居正 7min / 海瑞 12.5min |
| Agent 前台 bash 调 python subprocess | 未测试 | — | H2 的关键实验 |
| `run_in_background=true` + 纯 kimi CLI（无 python） | 未测试 | — | H1 的关键实验 |

### 16.3 三个候选假设

**H1（shell idle 回收）**：Claude Code 的 `run_in_background` shell 对长时间无 stdout 输出的进程有静默回收，约 2 分钟触发。进程被杀时通过 Windows Job Object 连带所有子孙进程一起 kill，因此 kimi 孙子进程、python 父进程、后台 shell 本体同时失效。

**H2（Python 中间层的信号/stdin 异常）**：Python 作为中间层在 Windows 后台 shell 下与 bash 进程有语义差异（stdin 归属、console handle、CTRL_* 事件接收），导致在 kimi 空闲等待网络响应期间被误判或误杀。

**H3（H1 + H2 耦合）**：两个因素都需要满足才会死。单纯 run_in_background 不死，单纯 python 包裹不死。

### 16.4 证据偏向 H1

- 用户明确反馈"之前调用都没问题，问题出在使用 Python 脚本以后" — 这里的"之前"很可能是 Agent 前台 bash 直调 Kimi 的成功路径，用户从没长时间后台运行过带 python 包裹的版本
- 前台 Agent 调 kimi CLI 能稳定跑 7–13 分钟（包含大段 stdout 静默期） — 说明 Kimi 本身长期无 stdout 不是问题，问题在后台 shell 的判定
- 两次死亡都严格卡在 2 分钟 — 这种确定性时间点更像定时器触发的回收，不像 subprocess 的 race condition

但 H2 不能完全排除，需要实测分离。

### 16.5 低成本验证方案（不消耗 Kimi 配额）

用 `sleep` 替代 kimi 做四组对照实验：

| 测试 | 命令（简化版） | 预期（若 H1 正确） |
|------|-------------|------------------|
| T1 | 前台 bash: `sleep 180 && echo done` | 活 |
| T2 | `run_in_background=true` + `bash -c "sleep 180 && echo done"` | **死于 2 分钟**（验证 H1） |
| T3 | `run_in_background=true` + `python -c "import time; time.sleep(180); print('alive')"` | 死于 2 分钟（与 T2 同因） |
| T4 | `run_in_background=true` + python 每 30s print 一行心跳 + subprocess sleep 180 | 活（若"心跳喂狗"能欺骗 idle 管理器） |

T2 是最关键的——如果 run_in_background 纯 bash 无 python 也会 2 分钟死，H1 确立，python 无关。T4 能验证心跳方案是否可行。

实验总耗时 < 10 分钟，零 API 成本，应在下次检修窗口安排一次。

### 16.6 当前生产解法（已采用）

**主窗口派 Agent → Agent 前台 bash 直调 kimi CLI**，13 人拆 7 批、每批 2 人并发。

优点：
- 验证稳定（张居正 + 海瑞 已成功）
- 不经 python 中间层，不走 run_in_background
- Agent 任务本身受 Agent 超时保护（约 25-30 分钟），单个 kimi 调用 < 15 分钟安全余量大

代价：
- 13 人不能一次性挂机跑完，需主窗口调度每批
- 每批间隔由 cron 15 分钟跟进驱动
- claude 主会话不能关

### 16.7 根治方向（优先级排序）

1. **立即可用**：Agent 前台阻塞（当前方案）——短期任务首选
2. **中期选项**：若 T4 心跳喂狗方案证实有效，给 `tools/batch_biography.py` 加一条"每 20 秒 print 一行 PING"的额外线程，再启用 `run_in_background` 可能就稳了
3. **长期根治**：用 `schtasks`/Windows 任务计划脱离 Claude 完全独立运行。这是**真正的跨 session 长任务**方案，适合 P2/P3 等更大批次（比如 45 份）
4. **不建议**：继续在 run_in_background 里加 subprocess 层修复——如果 H1 成立，所有 Python 层的修复都是治标

### 16.8 本节结论

- 第 15 节的修复（Popen 流式读 + 心跳文件）应保留，对"非 run_in_background 环境下的 python 长任务"仍有价值
- 但 P1 批次不走 Python 入口，直接 Agent + Kimi CLI
- 问题 D 的最终答案在未做完的 T1-T4 实验里。当前项目的工作优先级高于底层验证，实验推迟到收工后

