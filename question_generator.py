#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
小肥猫学习 - 智能出题模块
功能：
1. 根据7天数学专题循环 + KET英语路线图，每天自动生成新题目
2. 调用 DeepSeek API 生成结构化JSON题目
3. 混入错题本中的旧错题变式
4. 输出 daily_questions.json 供批改模块使用

用法：
  python3 question_generator.py              # 生成今天的题
  python3 question_generator.py --date 2026-05-15  # 指定日期
  python3 question_generator.py --preview     # 只预览不保存
"""

import json
import os
import sys
import re
import logging
from pathlib import Path
from datetime import datetime, timedelta

# ==================== 配置区 ====================

WORK_DIR = Path(__file__).parent.resolve()
DEEPSEEK_API_KEY = os.environ.get("DEEPSEEK_API_KEY", "sk-f5d41971d21d46ffbdd4e1d7af4a093c")
DEEPSEEK_BASE_URL = "https://api.deepseek.com"
DEEPSEEK_MODEL = "deepseek-chat"

# 课程起始日期（Day 1 从这天开始）
COURSE_START_DATE = "2026-05-14"

# 输出文件
DAILY_QUESTIONS_FILE = WORK_DIR / "daily_questions.json"
MISTAKE_BOOK_FILE = WORK_DIR / "mistake_book.json"

# 日志
LOG_DIR = WORK_DIR / ".logs"
LOG_FILE = LOG_DIR / "question_generator.log"

# ==================== 日志初始化 ====================
LOG_DIR.mkdir(exist_ok=True)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler(LOG_FILE, encoding="utf-8"),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger("question_generator")


# ==================== 数学专题定义（北师大版2026新版 三下 全册覆盖） ====================
# 教材完整目录（7个单元 + 复习）：
#   第一单元：乘法（两位数×两位数）
#   第二单元：除法（两/三位数÷一位数、除法验算）
#   第三单元：时间（年月日、24时计时法、计算经过时间）
#   第四单元：面积（面积概念、长方形正方形面积公式、面积单位换算）
#   第五单元：周长（周长概念、长方形正方形周长公式、逆运算求边长）
#   第六单元：认识分数（分数初步认识、简单同分母加减）
#   第七单元：数据的整理和表示（条形统计图、平均数初步）
#   综合实践：图形的运动（平移+轴对称）、观察物体、方向与位置
#
# 出题策略：14天大循环（每单元2天），Day 15 综合复盘
# 这样每个知识点都有足够深度，且覆盖全册所有考点

MATH_TOPICS = [
    # ===== 第一周：乘法 + 除法基础 =====
    {
        "day": 1,
        "unit": "第一单元·乘法",
        "name": "两位数 × 两位数竖式",
        "knowledge_points": [
            "两位数乘两位数竖式计算（不进位/一次进位/连续进位）",
            "乘法各部分名称：因数×因数=积",
            "估算：把两位数看成接近的整十数再算",
            "乘法验算：交换两个因数位置重算",
            "中间/末尾有0的乘法（如 102×23, 30×45）"
        ],
        "question_types": ["竖式计算", "填空", "判断", "改错", "应用题"],
        "description": "重点掌握十位上的数相乘后要加个位进上来的数；注意0占位的特殊情况"
    },
    {
        "day": 2,
        "unit": "第二单元·除法",
        "name": "两三位数除以一位数（竖式）",
        "knowledge_points": [
            "两/三位数÷一位数的竖式计算（无余数）",
            "两/三位数÷一位数的竖式计算（有余数）",
            "除法各部分名称：被除数÷除数=商…余数",
            "余数必须小于除数",
            "0除以任何非零数都得0；0不能做除数"
        ],
        "question_types": ["竖式计算", "填空", "判断", "改错", "应用题"],
        "description": "⚠️ 重点易错：商中间或末尾有0的情况（如 84÷4, 120÷3）；余数的处理"
    },
    # ===== 第二周：时间 + 面积基础 =====
    {
        "day": 3,
        "unit": "第三单元·时间",
        "name": "年月日与24时计时法",
        "knowledge_points": [
            "一年有12个月，大月(31天): 1/3/5/7/8/10/12月，小月(30天): 4/6/9/11月，平年2月28天/闰年2月29天",
            "闰年判断：能被4整除但不能被100整除，或能被400整除",
            "24时计时法与12时计时法的转换",
            "计算经过时间（结束时刻 - 开始时刻）",
            "日期推算（几天后是几月几号）"
        ],
        "question_types": ["填空", "选择", "判断", "计算（经过时间）", "实际应用"],
        "description": "⚠️ 重点易错：大月小月记混；跨天计算经过时间忘记加一天"
    },
    {
        "day": 4,
        "unit": "第四单元·面积（一）",
        "name": "面积概念与面积单位换算",
        "knowledge_points": [
            "面积的含义：物体表面或封闭图形的大小",
            "常用面积单位：cm²(平方厘米), dm²(平方分米), m²(平方米)",
            "相邻面积单位进率：1 dm² = 100 cm², 1 m² = 100 dm²",
            "大单位→小单位用乘法（×100），小单位→大单位用除法（÷100）",
            "⚠️ 面积 vs 周长的区别：面积是'大小'(平方)，周长是'长度'(一维)",
            "生活中对面积的感知：指甲盖约1cm²、手掌约1dm²、教室地面约50m²"
        ],
        "question_types": ["单位换算填空", "比较大小", "选择", "画图（区分面积和周长）", "实际估测"],
        "description": "⚠️ 核心难点：面积单位和长度单位的彻底区分（cm vs cm²）；进率100不是10！"
    },
    # ===== 第三周：面积公式 + 周长 =====
    {
        "day": 5,
        "unit": "第四单元·面积（二）",
        "name": "长方形和正方形面积计算",
        "knowledge_points": [
            "长方形面积 = 长 × 宽",
            "正方形面积 = 边长 × 边长",
            "已知面积求边长：边长 = 面积 ÷ 边长（正方形）；长/宽 = 面积 ÷ 宽/长（长方形）",
            "面积相同时周长可能不同（如面积都是16cm²的正方形和长方形）",
            "组合图形的面积（分割法/填补法）",
            "面积单位之间的复杂换算（m²↔dm²↔cm²）"
        ],
        "question_types": ["计算", "填空", "选择", "逆向求边长", "综合应用题"],
        "description": "含逆向思维题：给面积反求长/宽；面积与周长的对比分析"
    },
    {
        "day": 6,
        "unit": "第五单元·周长",
        "name": "正方形和长方形的周长",
        "knowledge_points": [
            "周长概念：封闭图形一周的长度",
            "长方形周长 = (长+宽)×2 或 长+宽+长+宽",
            "正方形周长 = 边长×4",
            "逆运算：已知周长求边长（长方形求长/宽、正方形求边长）",
            "单位统一后再计算（周长用长度单位！）",
            "不规则图形的周长（所有外围边长之和）"
        ],
        "question_types": ["计算", "填空", "选择", "画图（标长宽）", "应用题"],
        "description": "含逆向思维题：给周长反求边长；⚠️ 注意周长用长度单位(m/cm/dm)不是面积单位"
    },
    # ===== 第四周：分数 + 数据统计 =====
    {
        "day": 7,
        "unit": "第六单元·认识分数",
        "name": "分数的初步认识",
        "knowledge_points": [
            "分数的意义：把一个整体平均分成几份，每份就是它的几分之一",
            "分子、分母、分数线各自的含义",
            "读写简单的分数（真分数：分子<分母）",
            "比较同分母分数的大小（分母相同，分子大的分数大）",
            "分数的简单应用：部分与整体的关系（一半、三分之一等生活场景）"
        ],
        "question_types": ["看图写分数", "填空", "比较大小", "判断", "实际应用（分东西）"],
        "description": "'平均分'是关键关键词——不平均就不能用分数表示"
    },
    {
        "day": 8,
        "unit": "第六单元·分数运算 + 第三单元补充",
        "name": "简单同分母分数加减法 + 时间经过时间深化",
        "knowledge_points": [
            "同分母分数加法：分母不变，分子相加（如 1/5 + 2/5 = 3/5）",
            "同分母分数减法：分母不变，分子相减（如 4/5 - 1/5 = 3/5）",
            "1可以写成分子分母相同的分数（如 5/5 = 3/3 = 1）",
            "【时间深化】复杂的经过时间计算（跨整天、跨月份）",
            "【时间深化】列车时刻表/节目单的实际应用"
        ],
        "question_types": ["分数计算", "填空", "时间计算", "综合应用题"],
        "description": "分数加减只考同分母最简单的；时间计算加大难度到综合应用水平"
    },
    # ===== 第五周：数据统计 + 图形运动（平移/轴对称） =====
    {
        "day": 9,
        "unit": "第七单元·数据的整理和表示",
        "name": "条形统计图与平均数初步",
        "knowledge_points": [
            "认识条形统计图：纵轴刻度、横轴类别、直条高度对应数量",
            "读取条形统计图的信息（最多/最少/相差多少/一共多少）",
            "简单的数据收集与分类整理方法",
            "平均数的含义：移多补少，代表一组数据的整体水平",
            "计算平均数：总和 ÷ 个数（数据为整数且能整除）"
        ],
        "question_types": ["读图回答问题", "填空", "计算平均数", "绘制简单条形图（描述坐标）", "实际调查类应用题"],
        "description": "必须配统计图！用文字描述或ASCII画出条形图的坐标和数值"
    },
    {
        "day": 10,
        "unit": "综合实践·图形的运动",
        "name": "平移",
        "knowledge_points": [
            "平移的定义：物体沿直线运动，形状大小方向都不变",
            "平移两要素：方向（上下左右）和距离（格数）",
            "平移 vs 旋转的本质区别",
            "在方格纸上画出平移后的图形",
            "找对应点来确定整个图形的平移"
        ],
        "question_types": ["判断（平移还是旋转？）", "数格子填距离", "选择", "画图题", "描述平移过程"],
        "description": "必须配图形！用文字描述图形坐标或ASCII示意图"
    },
    # ===== 第六周：对称 + 方向位置 + 观察物体 =====
    {
        "day": 11,
        "unit": "综合实践·图形的运动",
        "name": "轴对称",
        "knowledge_points": [
            "轴对称图形的定义：沿一条直线对折两边完全重合",
            "对称轴：那条折痕所在的直线",
            "常见轴对称图形及对称轴数量（圆无数条、正方形4条、长方形2条、等边三角形3条）",
            "在方格纸上补全轴对称图形的另一半",
            "画对称轴"
        ],
        "question_types": ["判断是否为轴对称图形", "数对称轴数量", "选择", "画对称轴", "补全另一半"],
        "description": "必须配图形！用文字描述或ASCII表示图形"
    },
    {
        "day": 12,
        "unit": "综合实践·方向与位置 + 观察物体",
        "name": "方向、位置与观察物体",
        "knowledge_points": [
            "八个基本方向：东/南/西/北/东北/西北/东南/西南",
            "根据方向和距离确定位置（如'在学校的东北方向约200米处'）",
            "路线描述：从A出发向XX方向走X米到达B",
            "观察物体：从不同方向（正面/侧面/上面）观察立体图形，看到的面不同",
            "用小正方体搭立体图形并判断三视图"
        ],
        "question_types": ["填空（方向判断）", "选择", "画方位图（描述坐标）", "观察物体判断", "路线描述"],
        "description": "必须配图形！方向题需要给出参照物和方位图；观察物体需描述立体图形结构"
    },
    # ===== 第七周：混合运算 + 除法深化 =====
    {
        "day": 13,
        "unit": "第二单元·除法深化 + 混合运算",
        "name": "除法验算、混合运算与解决问题",
        "knowledge_points": [
            "除法验算：商×除数+余数=被除数（带余数除法验算）",
            "乘除混合运算顺序（从左到右）",
            "加减乘除四则混合运算（有括号先算括号内）",
            "解决两步计算的实际问题（先算什么再算什么）",
            "归一问题 / 归总问题的初步接触"
        ],
        "question_types": ["脱式计算", "填空", "两步应用题", "改错", "列式计算"],
        "description": "重点：理解运算优先级，能用综合算式表达解题思路"
    },
    {
        "day": 14,
        "unit": "第一~七单元·专项强化",
        "name": "易错点专项突破",
        "knowledge_points": [
            "乘法常见错误：进位漏加、0的处理、估算偏差",
            "除法常见错误：余数≥除数、商0漏写、验算错误",
            "面积vs周长混淆（最经典易错点！）",
            "时间计算陷阱（跨天、不同月份天数）",
            "分数'平均分'条件遗漏"
        ],
        "question_types": ["改错题（给出典型错解让学生找错）", "辨析题", "对比练习", "陷阱应用题"],
        "description": "全部是经典易错变式题！每道题都来自真实错题本中的高频错点模式"
    },
    # ===== Day 15：全册综合复盘 =====
    {
        "day": 15,
        "unit": "全册综合",
        "name": "全知识点综合复盘",
        "knowledge_points": [
            "前14天所有知识点的综合运用",
            "跨单元融合：如面积+周长综合、时间+乘法应用、统计+分数",
            "错题回炉：从错题本中选取高频错点出变式题",
            "两步以上复合应用题"
        ],
        "question_types": ["综合计算", "混合应用题", "错题变式", "开放思考"],
        "description": "综合测试，覆盖全册所有考点 + 错题本精选变式。难度略高于平时，模拟期末考试感觉"
    }
]

# 数学周期总天数（用于循环计算）
MATH_CYCLE_DAYS = len(MATH_TOPICS)  # 15天一个大循环


# ==================== 英语 KET 单词库 ====================

# 已掌握的39词（不再作为新词出题，仅复习）
MASTERED_VOCAB = [
    ("forget", "忘记"), ("remember", "记得"), ("worry", "担心"), ("realize", "意识到"),
    ("lose", "丢失"), ("leave", "落下"), ("arrive", "到达"), ("miss", "错过/想念"),
    ("hurry", "匆忙"), ("wait", "等待"),
    ("invite", "邀请"), ("plan", "计划"), ("prepare", "准备"), ("enjoy", "享受"),
    ("decide", "决定"), ("happen", "发生"),
    ("pencil box", "铅笔盒"), ("mathematics book", "数学书"), ("pencil case", "文具盒"),
    ("snack", "零食"), ("drink", "饮料"),
    ("friend", "朋友"), ("teacher", "老师"), ("grandparents", "祖父母"),
    ("countryside", "乡村"), ("picnic", "野餐"), ("fresh", "新鲜的"), ("quiet", "安静的"),
    ("climb", "攀爬"), ("pick", "采摘"), ("feed", "喂养"), ("rest", "休息"),
    ("share", "分享"), ("hill", "小山"),
    ("expensive", "昂贵的"), ("enough", "足够的"), ("lucky", "幸运的"), ("sorry", "抱歉的"),
]

# 新词池（按KET考纲分类，覆盖25+话题）
NEW_VOCAB_POOL = {
    # ===== P0 紧急（2周内启动）=====
    "P0_现在完成时": [
        ("have", "有/已经"), ("has", "有/已经(三单)"), ("been", "是(been)"), ("gone", "去(gone)"),
        ("done", "做(done)"), ("eaten", "吃(eaten)"), ("written", "写(written)"), ("seen", "看(seen)"),
        ("just", "刚刚"), ("already", "已经"), ("yet", "还(尚未)"), ("never", "从不"),
        ("ever", "曾经"), ("before", "以前"),
    ],
    "P0_将来时": [
        ("will", "将要"), ("shall", "将要"), ("tomorrow", "明天"), ("next week", "下周"),
        ("this evening", "今晚"), ("future", "未来"), ("soon", "不久"), ("later", "稍后"),
        ("promise", "承诺"), ("hope", "希望"), ("tonight", "今晚"), ("day after tomorrow", "后天"),
        ("next month", "下个月"), ("next year", "明年"),
    ],
    "P0_情态动词": [
        ("can", "能/会"), ("could", "能(过去式/更礼貌)"), ("should", "应该"),
        ("must", "必须"), ("may", "可以"), ("might", "可能"), ("needn't", "不必"),
        ("ability", "能力"), ("permission", "允许"), ("advice", "建议"), ("rule", "规则"),
        ("possible", "可能的"), ("important", "重要的"),
    ],
    # ===== P1 重要（1个月内）=====
    "P1_形容词比较级": [
        ("tall/taller/tallest", "高/更高/最高"), ("short/shorter/shortest", "短/更短/最短"),
        ("big/bigger/biggest", "大/更大/最大"), ("small/smaller/smallest", "小/更小/最小"),
        ("long/longer/longest", "长/更长/最长"), ("fast/faster/fastest", "快/更快/最快"),
        ("good/better/best", "好/更好/最好"), ("bad/worse/worst", "坏/更坏/最坏"),
        ("more", "更多"), ("most", "最多"), ("less", "更少"), ("least", "最少"),
        ("than", "比"), ("as...as", "...和...一样"),
    ],
    "P1_介词短语": [
        ("in", "在...里面"), ("on", "在...上面"), ("at", "在(地点/时间)"),
        ("under", "在...下面"), ("behind", "在...后面"), ("next to", "紧挨着"),
        ("between", "在...之间"), ("opposite", "在...对面"), ("near", "靠近"),
        ("in front of", "在...前面"), ("above", "在...上方"), ("below", "在...下方"),
        ("inside", "在里面"), ("outside", "在外面"), ("across from", "在...对面"),
    ],
    "P1_冠词": [
        ("a", "一个(辅音音素前)"), ("an", "一个(元音音素前)"), ("the", "这个/那个(定冠词)"),
    ],
    "P1_数字与数量": [
        ("first/second/third", "第一/第二/第三"), ("half", "一半"), ("quarter", "四分之一/一刻钟"),
        ("double", "双倍"), ("enough", "足够的"), ("every", "每个"), ("all", "所有的"),
        ("both", "两者都"), ("each", "每一个"), ("another", "另一个"),
    ],
    "P1_时间频率词": [
        ("always", "总是"), ("usually", "通常"), ("often", "经常"), ("sometimes", "有时"),
        ("rarely", "很少"), ("never", "从不"), ("once/twice", "一次/两次"),
        ("daily", "每天"), ("weekly", "每周"), ("weekend", "周末"),
    ],
    # ===== P2 日常穿插（长期推进）=====
    "P2_代词强化": [
        ("I/me/my/mine", "我/我/我的/我的东西"), ("you/your/yours", "你/你的/你的东西"),
        ("he/him/his", "他/他/他的"), ("she/her/hers", "她/她/她的"),
        ("it/its", "它/它的"), ("they/them/theirs", "他们/他们/他们的"),
        ("we/us/ours", "我们/我们/我们的"), ("ourselves", "我们自己"),
        ("themselves", "他们自己"), ("myself", "我自己"), ("someone", "某人"),
        ("everyone", "每个人"), ("nobody", "没有人"), ("something", "某事/某物"),
    ],
    "P2_同义替换": [
        ("buy", "买(=get/purchase)"), ("meet", "遇见(=see)"), ("have classes", "上课"),
        ("like", "喜欢(=enjoy/love)"), ("look at", "看(=watch)"), ("need", "需要(=want)"),
        ("say", "说(=tell/speak)"), ("find", "发现(=discover)"),
        ("take", "花费(时间)(=spend)"), ("call", "叫/打电话(=phone/ring)"),
        ("start", "开始(=begin)"), ("finish", "完成(=end/complete)"),
    ],
    "P2_日常场景地点": [
        ("airport", "机场"), ("station", "车站"), ("library", "图书馆"),
        ("hospital", "医院"), ("supermarket", "超市"), ("restaurant", "餐厅"),
        ("hotel", "酒店"), ("museum", "博物馆"), ("cinema", "电影院"),
        ("park", "公园"), ("beach", "海滩"), ("bridge", "桥"),
        ("pool", "游泳池"), ("garden", "花园"), ("market", "市场"),
        ("office", "办公室"), ("factory", "工厂"), ("farm", "农场"),
    ],
    "P2_学校与学习": [
        ("homework", "家庭作业"), ("classroom", "教室"), ("playground", "操场"),
        ("subject", "科目"), ("lesson", "课"), ("exercise", "练习"),
        ("exam/test", "考试/测试"), ("grade/score", "成绩/分数"),
        ("dictionary", "字典"), ("project", "项目/作业"), ("science", "科学"),
        ("history", "历史"), ("geography", "地理"), ("art", "美术"),
    ],
    "P2_家庭与人物关系": [
        ("parent", "父/母"), ("uncle", "叔叔/舅舅"), ("aunt", "阿姨/姑姑"),
        ("cousin", "堂/表兄弟姐妹"), ("nephew", "侄子/外甥"), ("niece", "侄女/外甥女"),
        ("neighbor", "邻居"), ("member", "成员"), ("family", "家庭"),
    ],
    "P2_食物与餐饮": [
        ("breakfast", "早餐"), ("lunch", "午餐"), ("dinner", "晚餐"),
        ("vegetable", "蔬菜"), ("fruit", "水果"), ("meat", "肉"),
        ("chicken", "鸡肉"), ("fish", "鱼"), ("rice", "米饭"),
        ("noodle(s)", "面条"), ("bread", "面包"), ("butter", "黄油"),
        ("coffee", "咖啡"), ("juice", "果汁"), ("water", "水"),
        ("menu", "菜单"), ("order", "点餐/订单"),
    ],
    "P2_衣物与穿戴": [
        ("uniform", "校服"), ("sweater", "毛衣"), ("jacket", "夹克"),
        ("coat", "外套"), ("shoe(s)", "鞋子"), ("shirt", "衬衫"),
        ("skirt", "裙子"), ("trousers/pants", "裤子"), ("hat/cap", "帽子"),
        ("size", "尺寸"), ("wear", "穿"), ("dress up", "打扮"),
    ],
    "P2_天气与季节": [
        ("sunny", "晴朗的"), ("cloudy", "多云的"), ("rainy", "下雨的"),
        ("windy", "多风的"), ("snowy", "下雪的"), ("warm", "温暖的"),
        ("cool", "凉爽的"), ("hot", "热的"), ("cold", "冷的"),
        ("spring", "春天"), ("summer", "夏天"), ("autumn/fall", "秋天"), ("winter", "冬天"),
        ("temperature", "温度"), ("degree", "度数"),
    ],
    "P2_交通出行": [
        ("bus", "公交车"), ("train", "火车"), ("plane/airplane", "飞机"),
        ("ship/boat", "船"), ("bike/bicycle", "自行车"), ("car", "汽车"),
        ("taxi", "出租车"), ("subway/metro", "地铁"), ("ticket", "票"),
        ("driver", "司机"), ("passenger", "乘客"), ("journey/trip", "旅行"),
        ("traffic", "交通"), ("late", "迟到"), ("catch", "赶上(车/飞机)"),
    ],
    "P2_动词高频短语": [
        ("get up", "起床"), ("go to bed", "上床睡觉"), ("go home", "回家"),
        ("look for", "寻找"), ("turn on/off", "打开/关闭"), ("put on", "穿上"),
        ("take off", "脱下/(飞机)起飞"), ("pick up", "捡起/接人"),
        ("wake up", "醒来"), ("give up", "放弃"), ("set up", "建立"),
        ("clean up", "打扫"), ("hurry up", "赶紧"), ("grow up", "长大"),
    ],
    "P2_形容词描述类": [
        ("beautiful", "美丽的"), ("comfortable", "舒适的"), ("dangerous", "危险的"),
        ("different", "不同的"), ("difficult/hard", "困难的"), ("easy", "容易的"),
        ("famous", "著名的"), ("interesting", "有趣的"), ("popular", "受欢迎的"),
        ("safe", "安全的"), ("special", "特殊的"), ("wonderful", "精彩的"),
    ],
    "P2_身体与健康": [
        ("headache", "头痛"), ("toothache", "牙痛"), ("stomachache", "胃痛"),
        ("fever", "发烧"), ("cold", "感冒"), ("tired", "累的"),
        ("health", "健康"), ("medicine", "药"), ("doctor", "医生"),
        ("nurse", "护士"), ("toothbrush", "牙刷"), ("wash", "洗"),
    ],
}

# ==================== 英语语法路线图 ====================

ENGLISH_GRAMMAR_ROADMAP = [
    # ===== 第1-3周：P0 紧急（真题最高频）=====
    {"week": 1, "topic": "现在完成时 have/has done", "level": "P0", "key_points": ["have/has + 过去分词", "already/yet/just/never/ever 的用法", "与一般过去式的区别", "have been to vs have gone to"]},
    {"week": 2, "topic": "一般将来时 will / be going to", "level": "P0", "key_points": ["will + 动原", "be going to + 动原", "时间状语: tomorrow/next week/tonight", "will vs be going to 的细微区别"]},
    {"week": 3, "topic": "情态动词 can/could/should/must/may", "level": "P0", "key_points": ["can表能力(过去式could)", "should表建议", "must表必须(mustn't表禁止)", "may/might表可能/许可"]},
    # ===== 第4-7周：P1 重要 =====
    {"week": 4, "topic": "形容词比较级与最高级", "level": "P1", "key_points": ["-er/-est规则变化", "不规则: good/better/best, bad/worse/worst, far/further/furthest", "than连接比较对象", "as...as 原级比较", "much/a little 修饰比较级"]},
    {"week": 5, "topic": "介词 in/on/at 系统梳理", "level": "P1", "key_points": ["in+大地点/月份/年份/早上下午晚上", "at+具体时间/地点(小)", "on+具体日期/星期/某天上午", "其他常用介词: by/with/from/to/for/of/about"]},
    {"week": 6, "topic": "冠词 a/an/the 零冠词", "level": "P1", "key_points": ["a/an 不定冠词(首次提及)", "the 定冠词特指(再次提及/唯一事物)", "零冠词规则(三餐球类运动学科)", "a vs an (看发音不看字母)"]},
    {"week": 7, "topic": "代词系统强化 + 指示代词", "level": "P1", "key_points": ["主格/宾格转换(I/me, he/him等)", "形容词性物主代词(my/your等) vs 名词性物主代词(mine/yours等)", "反身代词(myself/themselves等)", "this/that these/those 用法区别"]},
    # ===== 第8-10周：P2 日常穿插 + 综合应用 =====
    {"week": 8, "topic": "同义替换 + 连词扩展", "level": "P2", "key_points": ["buy=get/purchase, meet=see, say=tell/speak 等", "连词: when/if/before/after/because/so/and/but/or", "固定搭配: look at, wait for, listen to 等"]},
    {"week": 9, "topic": "There be 句型深化 + 数量表达", "level": "P2", "key_points": ["there is / there be 单复数匹配", "there was/were 过去式", "some/any/no 用法", "many/much(a lot of)/a few(a little) 区分", "how many/how much 提问"]},
    {"week": 10, "topic": "祈使句 + 现在进行时综合复习", "level": "P2", "key_points": ["肯定/否定祈使句(Don't.../Please.../Let's...)", "现在进行时巩固(am/is/are + doing)", "一般现在时第三人称单数(s/es)", "四种时态综合辨析与应用"],
     "description": "KET考试核心四大时态最终整合"},
    # ===== 第11周及之后：循环复习 =====
    # 超过路线图后自动循环
]


# ==================== 工具函数 ====================

def get_day_offset(target_date_str=None):
    """
    计算目标日期距课程开始的第几天 (1-based)
    返回 (day_offset, date_string)
    """
    if target_date_str:
        target = datetime.strptime(target_date_str, "%Y-%m-%d").date()
    else:
        target = datetime.now().date()

    start = datetime.strptime(COURSE_START_DATE, "%Y-%m-%d").date()
    offset = (target - start).days + 1  # 1-based
    return max(offset, 1), target.strftime("%Y-%m-%d")


def get_math_topic(day_offset):
    """根据天数获取当天数学专题（15天大循环）"""
    cycle_pos = (day_offset - 1) % MATH_CYCLE_DAYS  # 0-14
    return MATH_TOPICS[cycle_pos]


def get_english_topic(day_offset):
    """根据天数获取当天英语语法主题（每7天推进一个语法主题）"""
    # 每7天推进一个语法主题（与数学周期独立）
    week_index = (day_offset - 1) // 7  # 第几周 (0-based)
    roadmap_len = len(ENGLISH_GRAMMAR_ROADMAP)
    if week_index < roadmap_len:
        topic = ENGLISH_GRAMMAR_ROADMAP[week_index]
    else:
        # 超过路线图范围后循环 P2 内容或综合复习
        cycle_week = week_index % roadmap_len
        if cycle_week == 0:
            topic = {"topic": f"全阶段综合复习 (第{week_index+1}周)", "level": "综合", "key_points": ["前{roadmap_len}周所有语法点随机组合", "错题本高频错点变式", "KET真题模拟"]}
        else:
            topic = ENGLISH_GRAMMAR_ROADMAP[cycle_week].copy()
            topic["topic"] = f"{topic['topic']} — 回顾 (第{week_index+1}周)"
    return topic


def get_new_vocab_for_day(day_offset, count=10):
    """按顺序从新词池中取当天的10个新单词"""
    all_new_words = []
    for category, words in NEW_VOCAB_POOL.items():
        for word, meaning in words:
            all_new_words.append((word, meaning, category))

    # 根据日期偏移决定取哪些词（保证每天不同且可复现）
    start_idx = (day_offset - 1) * count
    selected = []
    for i in range(count):
        idx = (start_idx + i) % len(all_new_words)
        selected.append({
            "word": all_new_words[idx][0],
            "meaning": all_new_words[idx][1],
            "category": all_new_words[idx][2]
        })
    return selected


def read_mistakes():
    """读取错题本数据"""
    try:
        if MISTAKE_BOOK_FILE.exists():
            text = MISTAKE_BOOK_FILE.read_text(encoding="utf-8")
            if text.strip():
                data = json.loads(text)
                return data.get("mistakes", [])
    except Exception as e:
        logger.warning(f"读取错题本失败: {e}")
    return []


def get_review_mistakes(mistakes, max_count=2):
    """
    从错题中筛选需要今天复习的错题
    优先选 status=new 或 next_review_date 到期的
    """
    today = datetime.now().strftime("%Y-%m-%d")
    review_candidates = []

    for m in mistakes:
        status = m.get("status", "new")
        next_date = m.get("next_review_date", "")

        if status == "mastered":
            continue

        # 如果到了复习日期或者状态是新错题
        if status == "new" or (next_date and next_date <= today):
            review_candidates.append(m)

    # 取最近的几条
    return review_candidates[:max_count]


# ==================== DeepSeek API 调用 ====================

def call_deepseek_api(prompt: str, temperature=0.7) -> str:
    """调用 DeepSeek API 生成内容"""
    from openai import OpenAI

    client = OpenAI(
        api_key=DEEPSEEK_API_KEY,
        base_url=DEEPSEEK_BASE_URL
    )

    try:
        logger.info(f"调用 DeepSeek API 出题... (prompt长度: {len(prompt)})")
        response = client.chat.completions.create(
            model=DEEPSEEK_MODEL,
            messages=[
                {"role": "system", "content": "你是一个专业的三年级数学和KET英语出题助手。你必须严格按照要求的JSON格式输出，不要输出任何其他文字。"},
                {"role": "user", "content": prompt}
            ],
            temperature=temperature,
            max_tokens=4000
        )
        reply = response.choices[0].message.content.strip()
        logger.info(f"API 回复长度: {len(reply)}")
        return reply
    except Exception as e:
        logger.error(f"DeepSeek API 调用失败: {e}")
        raise


# ==================== 构建 Prompt ====================

def build_generation_prompt(day_offset, date_str, math_topic, english_topic, new_vocab, review_mistakes=None):
    """构建发给 DeepSeek 的出题 Prompt"""

    # 格式化新词汇
    vocab_text = "\n".join([f"  - {v['word']} ({v['meaning']}) 【{v['category']}】" for v in new_vocab])

    # 格式化已掌握词汇（用于复习穿插）
    mastered_sample = random_sample(MASTERED_VOCAB, 5)
    mastered_text = "\n".join([f"  - {w} ({m})" for w, m in mastered_sample])

    # 格式化错题复习信息
    review_text = ""
    if review_mistakes:
        review_text = "\n### 📒 旧错题复习要求\n请在今日题目中混入以下旧错点的**变式题**（至少1道）：\n\n"
        for m in review_mistakes:
            review_text += f"- **{m.get('subject','')}** | {m.get('knowledge_point','')} | 原题: {m.get('question_content','')}\n"
            review_text += f"  孩子答: {m.get('student_answer', '')} | 正确答案: {m.get('correct_answer', '')}\n"
            review_text += f"  错因: {m.get('error_reason', '')}\n\n"

    prompt = f"""请为深圳三年级下学期学生生成今天的学习题目。输出严格JSON格式，不要包含任何markdown标记或其他文字。

## 学生背景
- 年级：三年级下学期（2026春季学期）
- 数学教材：北师大版2026新版 三下
- 英语目标：KET(A2 Key)考试备考
- 当前水平：数学基础中等，英语基础一般（已掌握39个KET核心词 + 一般过去式/现在进行时基础）

## 今日课程信息
- 日期：{date_str}
- 课程第 {day_offset} 天
- 数学专题：{math_topic.get('unit', math_topic['name'])} - {math_topic['name']}（第{((day_offset-1)%MATH_CYCLE_DAYS)+1}天/共{MATH_CYCLE_DAYS}天循环）
- 英语语法主题：{english_topic['topic']}（{english_topic.get('level','')}级别）

## 📐 数学出题要求（{math_topic.get('unit', math_topic['name'])} - {math_topic['name']}）

### 知识点
{chr(10).join(f'- {kp}' for kp in math_topic['knowledge_points'])}

### 题型要求
- 预期题型：{' / '.join(math_topic['question_types'])}
- 共 **3-5 道**数学题
- 题目难度：适合三年级中等偏上水平
- 分值分配：每题2-6分，总分约12-20分
- ⚠️ 重要规则：
  1. 数字不要太大（乘法不超过99×99，加减不超过三位数）
  2. 必须有至少1道**应用题**（结合生活场景）
  3. 如果是图形题（平移/轴对称），必须在 content 中用 ASCII 字符画出示意图或详细描述图形坐标
  4. 不能全部是纯计算题，题型要多样化
  5. 应用题场景要贴近孩子日常生活（买文具、分水果、操场跑步等）

### 专项说明
{math_topic['description']}

## 📘 英语 KET 出题要求

### 今日新单词（10个）— 必须在题目中使用这些词
{vocab_text}
⚠️ 规则：这10个词是**全新的**，之前从未学过。要在造句/阅读/作文中用到。

### 已掌握单词（随机抽5个用于复习穿插）
{mastered_text}
⚠️ 规则：这些词已学过，可在题目中混入1-2道复习题。

### 语法主题：{english_topic['topic']}
要点：
{chr(10).join(f'- {kp}' for kp in english_topic.get('key_points', []))}

### 题型要求
- 共 **3-5 道**英语题
- 固定结构：
  1. **单词默写/翻译**（3分）— 考今日新词中的3-5个
  2. **语法填空/选择**（3-5分）— 考当日语法主题
  3. **句子翻译/造句**（4-6分）— 用新词+新语法造句
  4. 可选：**短文阅读理解简答**（4-6分）— 包含新词和新语法
  5. 可选：**KET小作文**（5-8分）— 55-65词，限定必用词汇
- 总分约15-25分
- 作文如果出的话，词数限制55-65词，给出限定必用词汇列表

{review_text}
## 🔧 输出格式（严格 JSON）

```json
{{
  "date": "{date_str}",
  "status": "pending_grading",
  "total_score": <数学总分+英语总分>,
  "math": {{
    "subject": "数学",
    "count": <数学题数>,
    "total_score": <数学总分>,
    "topic": "{math_topic['name']}",
    "questions": [
      {{
        "id": "M1",
        "num": 1,
        "type": "<题型>",
        "score": <分值>,
        "content": "<题目完整内容，如果是图形题要有ASCII图形或详细描述>",
        "correct_answer": "<正确答案>",
        "knowledge_point": "<知识点标签>",
        "explanation": "<详细解析，三年级学生能看懂>"
      }}
    ]
  }},
  "english": {{
    "subject": "英语KET",
    "count": <英语题数>,
    "total_score": <英语总分>,
    "grammar_topic": "{english_topic['topic']}",
    "new_words": [<当日10个新词列表>],
    "questions": [
      {{
        "id": "E1",
        "num": 1,
        "type": "<题型>",
        "score": <分值>,
        "content": "<题目完整内容>",
        "correct_answer": "<正确答案>",
        "answer_format": "<家长提交答案的格式提示。⚠️ 示例中绝对不能出现本题的真实答案！用'X'或'word1'等占位符代替>"
        "knowledge_point": "<知识点标签>",
        "explanation": "<详细解析>",
        "scoring_criteria": ["<评分要点1>", "<评分要点2>"]
      }}
    ]
  }},
  "_grading_notes": {{
    "parent_answer_format_hint": "家长通常用 |题号|答案| 格式提交答案。数学题答案通常是数字或算式；英语单词默写答案是英文单词（多个用/分隔）；语法填空答案用/分隔；作文是完整英文段落。",
    "common_variations": ["|M1|83|格式", "M1=83格式", "纯数字顺序格式", "科目前缀格式"]
  }}
}}
```

⚠️⚠️⚠️ 最后强调：
1. **只输出纯JSON**，不要用 ```json ``` 包裹
2. 数学应用题的场景要贴近三年级孩子生活
3. 英语作文如有，必须给出 scoring_criteria（评分标准）
4. correct_answer 字段必须是精确匹配的标准答案
5. explanation 要详细到孩子看了就能明白错在哪里
6. ⛔ **content 字段=纯题目**：不能包含任何答案、提示或解析！像真正的考试卷一样只用题目描述，需要配图时用ASCII字符画补充
7. ⛔ **answer_format 示例占位符**：只能用「word1, word2」或「句子A; 句子B」等通用占位符，绝不能出现本题的真实答案！"""

    return prompt


def random_sample(items, count):
    """简单随机取样（确定性种子基于日期）"""
    import random
    today_seed = int(datetime.now().strftime("%Y%m%d"))
    random.seed(today_seed)
    return random.sample(items, min(count, len(items)))


# ==================== JSON 清洗与验证 ====================

def clean_json_response(raw_response: str) -> dict:
    """清洗 API 返回的原始文本，提取有效 JSON"""
    text = raw_response.strip()

    # 尝试去掉 markdown code block
    if text.startswith("```"):
        # 找第一个 ``` 后面开始
        first_code = text.find("```")
        second_code = text.find("```", first_code + 3)
        if second_code > first_code:
            text = text[first_code+3:second_code]
            if text.startswith("json"):
                text = text[4:].strip()
        else:
            # 只有开头没有结尾
            text = re.sub(r'^```(?:json)?\s*', '', text)
            text = re.sub(r'```\s*$', '', text)

    text = text.strip()

    try:
        return json.loads(text)
    except json.JSONDecodeError:
        logger.warning("直接解析失败，尝试修复...")
        # 尝试找到 JSON 对象的起止位置
        start = text.find('{')
        end = text.rfind('}')
        if start != -1 and end != -1 and end > start:
            text = text[start:end+1]
            try:
                return json.loads(text)
            except json.JSONDecodeError as e:
                logger.error(f"JSON修复也失败了: {e}")
                raise ValueError(f"无法解析API返回的有效JSON: {text[:200]}...")
        raise ValueError(f"无法在响应中找到有效的JSON对象")


def validate_questions(data: dict) -> tuple[bool, str]:
    """验证生成的题目数据完整性"""
    errors = []

    if "math" not in data:
        errors.append("缺少 math 字段")
    else:
        if "questions" not in data["math"]:
            errors.append("math 缺少 questions")
        elif len(data["math"]["questions"]) == 0:
            errors.append("math questions 为空")
        else:
            for q in data["math"]["questions"]:
                for field in ["id", "num", "type", "score", "content", "correct_answer", "knowledge_point", "explanation"]:
                    if field not in q:
                        errors.append(f"math 题 {q.get('id','?')} 缺少 {field}")

    if "english" not in data:
        errors.append("缺少 english 字段")
    else:
        if "questions" not in data["english"]:
            errors.append("english 缺少 questions")
        elif len(data["english"]["questions"]) == 0:
            errors.append("english questions 为空")
        else:
            for q in data["english"]["questions"]:
                for field in ["id", "num", "type", "score", "content", "correct_answer", "knowledge_point", "explanation"]:
                    if field not in q:
                        errors.append(f"english 题 {q.get('id','?')} 缺少 {field}")

    if len(errors) > 0:
        return False, "; ".join(errors)
    return True, "OK"


# ==================== 主生成逻辑 ====================

def generate_daily_questions(target_date_str=None, preview_only=False):
    """
    主函数：生成一天的完整题目
    Args:
        target_date_str: 目标日期字符串 'YYYY-MM-DD'，默认今天
        preview_only: 仅预览不保存
    Returns:
        (success, data_dict or error_message)
    """
    # 1. 计算日期和偏移量
    day_offset, date_str = get_day_offset(target_date_str)
    logger.info(f"📅 生成日期: {date_str} | 课程第 {day_offset} 天")

    # 2. 获取当天专题
    math_topic = get_math_topic(day_offset)
    english_topic = get_english_topic(day_offset)
    new_vocab = get_new_vocab_for_day(day_offset, count=10)
    mistakes = read_mistakes()
    review_mistakes = get_review_mistakes(mistakes, max_count=2)

    logger.info(f"📐 数学专题: {math_topic['name']}")
    logger.info(f"📘 英语语法: {english_topic['topic']}")
    logger.info(f"📝 新词: {len(new_vocab)} 个")
    logger.info(f"📒 复习错题: {len(review_mistakes)} 条")

    # 3. 构建 prompt
    prompt = build_generation_prompt(
        day_offset, date_str, math_topic, english_topic,
        new_vocab, review_mistakes
    )

    # 4. 调用 AI 生成
    raw_reply = call_deepseek_api(prompt, temperature=0.7)

    # 5. 解析 JSON
    try:
        data = clean_json_response(raw_reply)
    except ValueError as e:
        logger.error(f"JSON 解析失败: {e}")
        logger.error(f"原始回复:\n{raw_reply[:500]}")
        return False, f"AI返回的数据格式异常，无法解析为JSON。原始回复:\n{raw_reply[:300]}..."

    # 6. 验证完整性
    is_valid, validation_msg = validate_questions(data)
    if not is_valid:
        logger.error(f"题目验证失败: {validation_msg}")
        return False, f"生成的题目数据不完整: {validation_msg}"

    # 7. 补充元信息
    data["date"] = date_str
    data["status"] = "pending_grading"
    data["generated_at"] = datetime.now().isoformat()
    data["day_offset"] = day_offset

    # 计算总分
    math_total = sum(q.get("score", 0) for q in data.get("math", {}).get("questions", []))
    eng_total = sum(q.get("score", 0) for q in data.get("english", {}).get("questions", []))
    data["total_score"] = math_total + eng_total
    data["math"]["total_score"] = math_total
    data["math"]["count"] = len(data["math"].get("questions", []))
    data["english"]["total_score"] = eng_total
    data["english"]["count"] = len(data["english"].get("questions", []))

    # 8. 保存或预览
    if preview_only:
        logger.info("✨ 预览模式 — 不保存文件")
        return True, data
    else:
        write_json_file(DAILY_QUESTIONS_FILE, data)
        logger.info(f"💾 已保存题目到: {DAILY_QUESTIONS_FILE}")

        # 打印摘要
        print(f"\n✅ 题目生成成功!")
        print(f"   📅 日期: {date_str} (第{day_offset}天)")
        print(f"   📐 数学: {data['math']['count']}题/{math_total}分 - {math_topic['name']}")
        print(f"   📘 英语: {data['english']['count']}题/{eng_total}分 - {english_topic['topic']}")
        print(f"   📊 总计: {data['total_score']}分")

        return True, data


def write_json_file(filepath: Path, data: dict):
    """安全写入 JSON 文件"""
    filepath.write_text(
        json.dumps(data, ensure_ascii=False, indent=2),
        encoding="utf-8"
    )


# ==================== CLI 入口 ====================

if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="小肥猫学习 - 智能出题模块")
    parser.add_argument("--date", type=str, default=None, help="指定日期 YYYY-MM-DD（默认今天）")
    parser.add_argument("--preview", action="store_true", help="只预览不保存")

    args = parser.parse_args()

    success, result = generate_daily_questions(
        target_date_str=args.date,
        preview_only=args.preview
    )

    if not success:
        print(f"\n❌ 出题失败: {result}")
        sys.exit(1)

    if args.preview:
        print(json.dumps(result, ensure_ascii=False, indent=2))
