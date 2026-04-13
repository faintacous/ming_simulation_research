import subprocess
import concurrent.futures
import os
import time

BASE_DIR = "G:/AIDev/Ming_Simulation_Research"
TARGET_DIR = os.path.join(BASE_DIR, "人物名录")

PERSONS = [
    ("李太后", "孝定太后，万历帝生母，隆庆帝皇贵妃，垂帘辅政支持张居正", "后妃宗室"),
    # 郑贵妃 已完成 (295行, 跳过)
    ("朱常洛", "明光宗/泰昌帝，万历帝长子，一月天子，国本之争核心", "后妃宗室"),
    # 王喜姐 已完成 (206行, 跳过)
    ("三娘子", "钟金哈屯，俺答汗夫人，封贡斡旋者，明廷封忠顺夫人", "后妃宗室"),
    ("朱翊镠", "潞简王，明穆宗第四子，万历帝同母弟", "后妃宗室"),
    ("王崇古", "宣大总督、兵部尚书，俺答封贡直接操盘手", "阁臣重臣"),
    ("严世蕃", "严嵩之子，实际操控朝政，以通倭罪被斩", "阁臣重臣"),
    ("张四维", "内阁首辅（1582-1583），蒲州人，出身晋商家族", "阁臣重臣"),
    ("王锡爵", "内阁首辅（1593-1594），国本之争中被逼退", "阁臣重臣"),
    ("沈一贯", "内阁首辅（1601-1606），浙党首领", "阁臣重臣"),
    ("叶向高", "内阁首辅，万历末年独相，天启初众正盈朝", "阁臣重臣"),
    ("方从哲", "内阁首辅（1613-1620），浙党，万历末年独撑大局", "阁臣重臣"),
    ("李春芳", "内阁首辅（1568-1571），隆庆朝过渡性首辅", "阁臣重臣"),
    ("赵志皋", "内阁首辅（1594-1601），万历怠政时期权力萎缩", "阁臣重臣"),
    ("顾宪成", "吏部文选司郎中，东林书院创始人，东林党精神领袖", "中下层文官"),
    ("杨继盛", "兵部员外郎，上疏弹劾严嵩五奸十大罪，被冤杀", "中下层文官"),
    ("邹元标", "左都御史，反对张居正夺情被廷杖，东林三君之一", "中下层文官"),
    ("高攀龙", "左都御史，东林书院创始人之一，投水自尽", "中下层文官"),
    ("赵南星", "吏部尚书，东林三君之一", "中下层文官"),
    ("潘季驯", "河道总督，治黄名臣，束水攻沙战略", "中下层文官"),
    ("方逢时", "大同巡抚，俺答封贡重要参与者", "中下层文官"),
    ("陈矩", "司礼监掌印太监，万历朝贤宦代表", "宦官"),
    ("胡宗宪", "浙直总督，嘉靖朝抗倭总指挥，剿抚并用", "武将"),
    ("谭纶", "蓟辽保定总督、兵部尚书，戚继光的直接上级", "武将"),
    ("李如松", "提督，平壤大捷主帅，李成梁长子", "武将"),
    ("马芳", "宣府总兵，出身卑微的北边名将", "武将"),
    ("刘綎", "总兵，参加朝鲜之役和播州之役，萨尔浒之战战死", "武将"),
    ("陈璘", "水师提督，露梁海战明方指挥官", "武将"),
    ("麻贵", "总兵/提督，朝鲜之役第二次入朝主帅", "武将"),
    ("邓子龙", "明军老将，露梁海战中殉国", "武将"),
    ("徐渭", "画家、书法家、戏曲家、军事参谋，多面天才", "文人学者"),
    ("李时珍", "医学家，《本草纲目》作者", "文人学者"),
    ("归有光", "散文家，以古文笔法入八股，唐宋派代表", "文人学者"),
    ("董其昌", "画家、书法家、鉴赏家，南北宗论提出者", "文人学者"),
    ("王畿", "阳明弟子，提出四无说，浙中王学代表", "文人学者"),
    ("朱载堉", "郑藩世子，音乐家、数学家，十二平均律发明者", "文人学者"),
    ("何心隐", "泰州学派激进人物，建聚和堂改革乡里，被捕杀", "文人学者"),
    ("汪直", "海商/走私集团首领，自称净海王徽王", "商人"),
    ("杨应龙", "播州宣慰使（土司），起兵反明，海龙囤陷落后自缢", "起义与叛乱者"),
    ("紫柏真可", "明末四大高僧之一，复兴佛教", "宗教人物"),
    ("憨山德清", "明末四大高僧之一，曹溪中兴祖", "宗教人物"),
    ("莲池袾宏", "明末四大高僧之一，净土宗大师", "宗教人物"),
    ("利玛窦", "意大利耶稣会传教士 Matteo Ricci，西学东渐核心人物", "外国人物"),
    ("丰臣秀吉", "日本太阁，统一日本后发动侵朝战争", "外国人物"),
    ("努尔哈赤", "后金大汗，建州女真统一者，八旗制度建立者", "外国人物"),
    ("李舜臣", "朝鲜水军将领，龟船战术，露梁海战殉国", "外国人物"),
    ("小西行长", "日军第一军团指挥官，朝鲜之役先锋", "外国人物"),
    ("范礼安", "Alessandro Valignano，耶稣会远东视察员", "外国人物"),
]

PROMPT_TEMPLATE = """请搜集并研究{name}（{identity}）的完整生平档案。这是严肃的历史研究。

【必须遵守】
1. 必须使用 WebFetch 和 WebSearch 工具从互联网搜集资料，来源包括：
   - 百度百科（简体URL）、维基百科中文（繁体URL，注意繁简转换）
   - 维基文库（古籍原文）、中国哲学书电子化计划 ctext.org
   - 国学大师、学术论文
2. 每条关键事迹必须标注真实来源（URL 或古籍卷次）
3. 不要凭训练知识编造，不确定的在"存疑 & 待查"区标注

【工具约束】
- 禁止使用 Agent 工具启动子代理（曾导致卡死）
- 禁止使用 question/ask 工具询问用户任何问题——本任务是无人值守批处理，没有用户可以回答。直接按以下要求执行
- 不要读取项目本地任何文件，包括 人物名录/ 目录——所有资料从网上获取
- 允许：WebFetch、WebSearch、Write、Bash

【执行规则】
- 不要询问澄清问题，不要请求确认，直接开始研究并写入最终文件
- 如果对研究范围有疑问，默认选择"最全面"的方案

【内容密度要求】（硬约束）
- 必须查阅至少 5 个独立来源（至少 2 个古籍卷次 + 2 个百科条目 + 1 个其他）
- 关键事迹至少 20 条，按时间排序
- 存疑区至少列 5 条待考问题

【必填章节】（每项都要有实质内容，不要空着）
- 家族背景与师承
- 主要官职/事业阶段
- 性格与逸闻轶事
- 重要著作/战功/政绩
- 人际网络（友、敌、门生）

【写作要求】
- 聚焦 1550-1600 年间的活动，但完整覆盖其一生
- 将结果写入 人物名录/{category}/{name}.md

文件格式：

# {name}

## 基本信息
- 生卒：
- 字号：
- 籍贯：
- 身份/官职：

## 生平概要
（1000字以内）

## 关键事迹
（至少20条，每条标注来源 URL 或古籍卷次）

## 重要人际关系
- 家族：
- 师承/门生：
- 盟友/同党：
- 对手/政敌：

## 主要著作/战功/政绩

## 性格与逸闻

## 历史评价
### 时人评价
### 后世评价

## 存疑 & 待查
（至少5条）

## 来源汇总
（列出所有 URL 和古籍卷次，至少5个独立来源）
"""

OPENCODE_BIN = "C:/Users/pc/AppData/Roaming/npm/opencode.cmd"

def run_person(name, identity, category):
    prompt = PROMPT_TEMPLATE.format(name=name, identity=identity, category=category)
    cmd = [
        OPENCODE_BIN, "run",
        "-m", "zhipuai-coding-plan/glm-5.1",
        "--dir", BASE_DIR,
        prompt
    ]
    start = time.time()
    target_file = os.path.join(TARGET_DIR, category, f"{name}.md")

    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=1800, encoding='utf-8', errors='replace')
        elapsed = time.time() - start

        if os.path.exists(target_file):
            with open(target_file, encoding='utf-8') as f:
                content = f.read()
            line_count = len(content.splitlines())
            url_count = content.count('http')
            print(f"[OK] {name} ({category}) - {line_count}行, {url_count} URL, {elapsed:.0f}秒", flush=True)
            return (name, category, "OK", line_count, url_count, elapsed)
        else:
            print(f"[FAIL] {name} - 文件未生成 ({elapsed:.0f}秒)", flush=True)
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
    print(f"开始批量搜集 {len(PERSONS)} 位人物传记...")
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
        avg_urls = total_urls / len(ok)
        print(f"总行数: {total_lines}, 平均每人URL数: {avg_urls:.1f}")
    fail = [r for r in results if r[2] != "OK"]
    if fail:
        print(f"失败: {[r[0] for r in fail]}")
