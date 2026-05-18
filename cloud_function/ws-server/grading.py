"""
小肥猫学习 - 智能批改核心逻辑 v2
==============================
v2 新特性（2026-05-18）：
1. LLM智能答案解析 - 自动识别各种非标准答案格式（句子/中文数字/混合格式）
2. LLM深度批改 + 分级评分 - 每道题都由AI分析，支持全分/半分/零分
3. 动态规则系统 - 家长通过飞书回复即可调整批改规则，无需重启服务
4. 修改建议反馈 - 回复"调整：XXX"自动解析并存入规则

与 v1 的接口兼容：grade_submission / format_grading_card / is_command 保持不变。
"""

import os
import re
import json
import logging
from datetime import datetime
from pathlib import Path

from openai import OpenAI

logger = logging.getLogger("grading")

# ==================== 配置（从环境变量读取） ====================

DEEPSEEK_API_KEY = os.environ.get("DEEPSEEK_API_KEY", "")
DEEPSEEK_BASE_URL = "https://api.deepseek.com"
DEEPSEEK_MODEL = "deepseek-chat"

BITABLE_APP_TOKEN = os.environ.get("BITABLE_APP_TOKEN", "")
BITABLE_DAILY_TABLE_ID = os.environ.get("BITABLE_DAILY_TABLE_ID", "")
BITABLE_MISTAKE_TABLE_ID = os.environ.get("BITABLE_MISTAKE_TABLE_ID", "")

# 规则文件路径（与 grading.py 同级的 grading_rules.json，或项目根目录）
_RULES_PATH = Path(__file__).resolve().parent.parent.parent / "grading_rules.json"

# 规则缓存（每次请求重新加载，确保最新）
_rules_cache = None
_rules_cache_time = 0


# ==================== 动态规则系统 ====================

def load_grading_rules(force_reload: bool = False) -> list:
    """加载批改规则，每次调用检查文件是否更新（轻量缓存）"""
    global _rules_cache, _rules_cache_time

    if not _RULES_PATH.exists():
        logger.warning(f"规则文件不存在: {_RULES_PATH}，使用内置默认规则")
        return _builtin_default_rules()

    mtime = _RULES_PATH.stat().st_mtime
    if not force_reload and _rules_cache is not None and mtime == _rules_cache_time:
        return _rules_cache

    try:
        with open(_RULES_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
        rules = [r for r in data.get("rules", []) if r.get("active", False)]
        _rules_cache = rules
        _rules_cache_time = mtime
        logger.info(f"已加载 {len(rules)} 条活跃规则")
        return rules
    except Exception as e:
        logger.error(f"加载规则失败: {e}")
        return _builtin_default_rules()


def _builtin_default_rules() -> list:
    """内置应急默认规则（文件不存在或损坏时使用）"""
    return [
        {"id": "builtin_001", "rule": "理解孩子答案含义后再判断对错，不因格式差异判错"},
        {"id": "builtin_002", "rule": "支持分级评分：全对/半对/全错"},
        {"id": "builtin_003", "rule": "每道题必须给出错因分析和改进建议"},
    ]


def _rules_to_prompt(rules: list, subject: str = "all") -> str:
    """将规则转换为 AI 提示词段落"""
    filtered = [r for r in rules if r.get("subject", "all") in ("all", subject)]
    if not filtered:
        return "（无特殊规则，按常规标准批改）"

    lines = []
    for i, r in enumerate(filtered, 1):
        rule_text = r.get("rule", "")
        lines.append(f"{i}. {rule_text}")
    return "\n".join(lines)


def save_grading_rules(rules: list, meta_update: dict = None) -> bool:
    """保存规则到文件（由修改建议处理流程调用）"""
    try:
        if _RULES_PATH.exists():
            with open(_RULES_PATH, "r", encoding="utf-8") as f:
                data = json.load(f)
        else:
            data = {"rules": []}

        data["rules"] = rules
        if meta_update:
            data.setdefault("_meta", {}).update(meta_update)
        data.setdefault("_meta", {})["last_updated"] = datetime.now().strftime("%Y-%m-%d %H:%M")

        with open(_RULES_PATH, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)

        # 清除缓存，下次请求重新加载
        global _rules_cache
        _rules_cache = None
        logger.info(f"已保存 {len(rules)} 条规则到 {_RULES_PATH}")
        return True
    except Exception as e:
        logger.error(f"保存规则失败: {e}")
        return False


def add_grading_rule(subject: str, rule_type: str, rule_text: str, priority: str = "medium", source: str = "feishu_feedback") -> dict:
    """新增一条批改规则，返回新规则对象"""
    rules = load_grading_rules(force_reload=True)
    # 避免重复：检查完全相同的规则
    for existing in rules:
        if existing.get("rule", "").strip() == rule_text.strip():
            return {"success": False, "message": "该规则已存在", "rule": existing}

    new_rule = {
        "id": f"rule_{datetime.now().strftime('%Y%m%d%H%M%S')}_{len(rules)+1}",
        "subject": subject,
        "type": rule_type,
        "rule": rule_text,
        "priority": priority,
        "active": True,
        "created": datetime.now().strftime("%Y-%m-%d"),
        "source": source,
    }
    rules.append(new_rule)
    save_grading_rules(rules)
    return {"success": True, "message": f"已添加规则: {rule_text[:50]}...", "rule": new_rule}


def delete_grading_rule(rule_id: str) -> dict:
    """删除/停用一条批改规则"""
    rules = load_grading_rules(force_reload=True)
    for r in rules:
        if r.get("id") == rule_id:
            r["active"] = False
            save_grading_rules(rules)
            return {"success": True, "message": f"已停用规则: {rule_id}"}
    return {"success": False, "message": f"未找到规则: {rule_id}"}


# ==================== LLM 答案解析 v2 ====================

def parse_answers_with_ai(message_text: str, questions: list) -> dict:
    """
    使用 LLM 智能解析答案 - v2 新功能
    ==================================
    不再依赖固定格式（|管道|、M1=83、纯数值），而是将完整的消息文本 +
    所有题目信息发给 DeepSeek，让 AI 理解孩子答案与题目的对应关系。

    支持场景：
    - 句子形式："第一题是83，第二题44，第三题一共63页剩22页"
    - 混合格式："83 44 forget，翻译题写的是：I missed..."
    - 中文数字："六十三页 二十二页"
    - 非标准表述："凑了一下是63，还剩22"（AI理解=答案63和22）

    Returns: {question_id: student_answer, ...}
    """
    if not DEEPSEEK_API_KEY:
        logger.warning("DeepSeek API 未配置，降级为规则解析")
        return _parse_answers_fallback(message_text, questions)

    # 构建题目列表给 AI
    questions_text = ""
    for i, q in enumerate(questions):
        qid = q.get("id", f"Q{i+1}")
        qtype = q.get("type", "")
        qcontent = q.get("content", "")[:120]
        questions_text += f"  [{qid}] ({qtype}) {qcontent}\n"

    prompt = f"""你是一个三年级作业答案解析助手。孩子的答案可能格式不标准（句子、中文数字、缩写等），你需要理解含义后匹配对应题目。

今日题目列表：
{questions_text}

孩子提交的答案文本：
{message_text}

请分析孩子的答案，将每道题对应的答案提取出来。
如果某题孩子未作答，标记为"未作答"。
如果孩子的答案包含多部分（如应用题的两问），用逗号或分号分隔。

严格按以下 JSON 格式输出，不要输出其他内容：
{{
  "answers": {{
    "M1": "孩子对第M1题的答案",
    "M2": "孩子对第M2题的答案",
    ...
  }},
  "notes": "对答案格式的简要说明（如有特殊情况）"
}}

重要原则：
- 理解孩子答案的含义，而不是机械匹配字符串
- 中文数字（如"六十三"）等价于阿拉伯数字（63）
- 如果答案不完整（如只答了应用题的一部分），如实记录
- 不要编造答案，只提取孩子真正写的内容"""

    try:
        client = OpenAI(api_key=DEEPSEEK_API_KEY, base_url=DEEPSEEK_BASE_URL)
        resp = client.chat.completions.create(
            model=DEEPSEEK_MODEL,
            messages=[
                {"role": "system", "content": "你是作业答案解析助手。只输出JSON，不要说其他话。"},
                {"role": "user", "content": prompt},
            ],
            temperature=0.0,
            max_tokens=1000,
        )
        text = resp.choices[0].message.content.strip()

        # 清理 markdown 代码块
        if text.startswith("```"):
            text = re.sub(r'^```(?:json)?\s*', '', text)
            text = re.sub(r'\s*```$', '', text)

        result = json.loads(text)
        answers = result.get("answers", {})
        logger.info(f"[AI解析] 从 {len(questions)} 道题中识别出 {len(answers)} 个答案")
        if result.get("notes"):
            logger.info(f"[AI解析] 备注: {result['notes']}")
        return _normalize_answer_keys(answers, questions)

    except Exception as e:
        logger.error(f"AI答案解析失败: {e}，降级为规则解析")
        return _parse_answers_fallback(message_text, questions)


def _normalize_answer_keys(answers: dict, questions: list) -> dict:
    """标准化答案字典的键名，确保与题目ID匹配"""
    normalized = {}
    # 构建题号到ID的映射
    id_map = {q.get("id", ""): q.get("id", "") for q in questions}
    id_map.update({str(q.get("num", "")): q.get("id", "") for q in questions})

    for key, val in answers.items():
        clean_key = str(key).strip().upper()
        if clean_key in id_map:
            normalized[id_map[clean_key]] = str(val)
        else:
            # 尝试模糊匹配
            for q in questions:
                qid = q.get("id", "")
                if str(q.get("num", "")) == clean_key:
                    normalized[qid] = str(val)
                    break
            else:
                normalized[clean_key] = str(val)
    return normalized


def _parse_answers_fallback(message_text: str, questions: list) -> dict:
    """降级方案：原有规则解析（当 AI 不可用时）"""
    message_text = message_text.strip()
    parsed = {}

    # 格式1: |题号|答案|
    if "|" in message_text and re.search(r'\|\d+\|', message_text):
        parts = message_text.split("|")
        i = 0
        while i < len(parts) - 1:
            part = parts[i].strip()
            if part.isdigit():
                num = int(part)
                answer = parts[i+1].strip() if i+1 < len(parts) else ""
                parsed[num] = answer
                i += 2
            else:
                i += 1
        return parsed

    # 格式2: M1=83 M2=44 E1=forget
    if "=" in message_text:
        for match in re.finditer(r'([MEmM][\d]+)\s*=\s*([^\s,，]+(?:[\s,，/]+[^\s=,，]+)*)', message_text):
            key = match.group(1).upper()
            val = match.group(2).strip()
            parsed[key] = val
        return parsed

    # 格式3: 纯数值按顺序匹配
    tokens = re.split(r'[\s,，]+', message_text)
    tokens = [t.strip() for t in tokens if t.strip()]
    for i, q in enumerate(questions):
        if i < len(tokens):
            q_id = q.get("id", f"Q{i+1}")
            parsed[q_id] = tokens[i]

    return parsed


# 保留旧函数名作为降级兼容
parse_answers = _parse_answers_fallback


# ==================== 考试评分标准 v2.1 ====================

def _ket_scoring_standard(q_type: str) -> str:
    """KET A2 英语考试评分标准（三年级适用）"""
    base = """【KET A2 语法评分标准 - 三年级适用】

一、语法填空题/选择题/改错题：
1. 时态错误 → 直接判错(0.0分)。必须指出：
   - 该用哪个时态，孩子用了哪个时态
   - 正确形式是什么
   - 给一个同类正确案例
2. 词形变种（如把went写成goed、better写成gooder）→ 直接判错(0.0分)
   - 指出这是不规则变化，给出正确形式
3. 主谓不一致（如He go to school）→ 判错(0.0分)
4. 单复数错误 → 视题目重点：考单复数则0分，否则0.5分
5. 冠词/介词小错（a/an/the混用）→ 0.5分
6. 大小写、标点错误 → 仅指出，不扣分
7. 拼写差1个字母（如forgit而非forget）→ 0.5分，指出正确拼写

二、翻译题：
1. 核心动词翻译正确 + 时态正确 = 全分
2. 核心动词正确但时态错误 = 0分
3. 修饰词小误差 = 0.5分
4. 不要求逐词一致，但时态和语态必须正确

三、写作题（如KET看图写话）：
1. 时态一致（全篇统一过去时或现在时）= 核心得分点
2. 内容要点齐全 = 及格线
3. 拼写、语法小错每处扣0.5分

输出要求：每道错题必须附带一个完整正确的案例示范句子"""
    return base


def _math_scoring_standard(q_type: str) -> str:
    """小学数学考试评分标准（三年级适用）"""
    base = """【小学数学考试评分标准 - 三年级适用】

一、计算题（口算/竖式/脱式）：
1. 结果完全正确、格式规范 → 全分(1.0)
2. 列式（方法）正确但计算出错 → 半分(0.5)
   - 必须指出具体哪一步计算错了
   - 展示正确的完整计算过程
3. 方法错误或全不会 → 0分(0.0)

二、应用题/解决问题：
1. 列式+计算+答案+单位全部正确 → 全分(1.0)
2. 思路正确、列式正确但计算错 → 半分(0.5)
   - 演示正确的计算步骤
3. 答案正确但漏写单位或写错单位 → 半分(0.5)
   - 提醒补充正确单位
4. 答案正确但缺少"答：" → 指出但不扣分
5. 完全不会或列式错误 → 0分(0.0)
   - 给出完整的解题步骤示范

三、填空题/选择题：
1. 答案正确 → 全分(1.0)
2. 答案错误 → 0分(0.0)
3. 数值正确但格式不符（如写了单位在填空里）→ 不扣分

四、格式兼容：
- 中文数字（六十三）与阿拉伯数字（63）等效
- 答案用句子描述（"一共看了63页，还剩22页"）→ 提取数值后评分

输出要求：每道错题必须附带正确的完整计算步骤示范"""
    return base


# ==================== LLM 深度批改 v2.1 ====================

def deep_grade_with_ai(question: dict, student_answer: str, rules: list) -> dict:
    """
    LLM 深度批改 - v2.1 核心
    =========================
    每道题都由 AI 进行深度分析，不再先匹配再走AI。
    注入当前活跃的批改规则，支持分级评分。
    v2.1: 按 KET 和小学数学考试标准评分，英语语法时态错误必须指出+案例。

    Returns: {
        "correct": bool,
        "score_ratio": 1.0 | 0.5 | 0.0,
        "analysis": str,       # 详细解析（含对错说明+知识点）
        "error_reason": str,   # 具体错因（指出错误点+正确形式）
        "improvement": str,    # 改进建议+记忆技巧
        "child_thinking": str, # 思维过程推测
        "example": str,        # v2.1新增：正确案例示范
    }
    """
    if not DEEPSEEK_API_KEY:
        correct_answer = str(question.get("correct_answer", ""))
        normalized_student = student_answer.strip().lower().replace(" ", "")
        normalized_correct = correct_answer.strip().lower().replace(" ", "")
        is_correct = normalized_student == normalized_correct
        return {
            "correct": is_correct,
            "score_ratio": 1.0 if is_correct else 0.0,
            "analysis": "答案正确" if is_correct else f"正确答案应为: {correct_answer}",
            "error_reason": "" if is_correct else "答案与标准答案不一致",
            "improvement": "" if is_correct else "请核对标准答案并理解解题思路",
            "child_thinking": "",
            "example": "",
        }

    q_subject = question.get("subject", "all")
    q_type = question.get("type", "")
    rules_prompt = _rules_to_prompt(rules, q_subject)

    # 根据科目和题型生成针对性的考试评分标准
    scoring_standard = _ket_scoring_standard(q_type) if "英语" in q_subject else _math_scoring_standard(q_type)

    prompt = f"""你是一个三年级小学数学和英语KET批改专家。请严格按照以下考试标准，**独立判断**孩子答案是否正确。

=== 题目信息 ===
题号: {question.get('id', '')}
题型: {question.get('type', '')}
题目内容: {question.get('content', '')}
知识点: {question.get('knowledge_point', '')}

=== 孩子答案 ===
{student_answer}

=== 当前批改规则（必须遵守） ===
{rules_prompt}

=== {q_subject}考试评分标准（必须严格遵守） ===
{scoring_standard}

=== 批改输出要求 ===
1. **你必须自己计算出/推演出正确答案**，不要依赖任何外部提供的"标准答案"。如果题目本身有明确答案（如数学计算、英语语法填空），你完全可以独立判断。
2. 先判断对错，再按上述标准给分（1.0 / 0.5 / 0.0）
3. 解析要具体到错误点：不是笼统说"不对"，而是说"你把went写成了goed，go的过去式是不规则变化，正确形式是went"
4. 英语语法错题必须给出正确案例示范（一个完整的正确句子）
5. 数学错题必须展示正确的完整计算步骤
6. 用三年级孩子能理解的语言，但专业度不降低
7. 对于开放性题目（翻译、阅读简答），判断答案是否合理达意，不要求逐字匹配

严格按JSON格式输出（不要markdown代码块）：
{{
  "correct": true/false,
  "score_ratio": 1.0/0.5/0.0,
  "analysis": "详细解析：(1)孩子的答案是什么 (2)正确答案应该是什么 (3)为什么对/错 (4)涉及的知识点（80-150字）",
  "error_reason": "具体错因：指出哪个词/哪个步骤错了，正确的应该是什么（50-100字）",
  "improvement": "改进建议：给一个记忆口诀或小技巧，三年级能懂（40-80字）",
  "child_thinking": "推测孩子可能的思考过程（20-40字）",
  "example": "正确案例示范：一个类似的完整正确例子（30-60字）"
}}"""

    try:
        client = OpenAI(api_key=DEEPSEEK_API_KEY, base_url=DEEPSEEK_BASE_URL)
        resp = client.chat.completions.create(
            model=DEEPSEEK_MODEL,
            messages=[
                {"role": "system", "content": "你是三年级KET/数学批改专家。只输出JSON。用温和鼓励的语气，但评分严格。英语语法时态错误必须判错。"},
                {"role": "user", "content": prompt},
            ],
            temperature=0.1,
            max_tokens=1000,
        )
        text = resp.choices[0].message.content.strip()

        if text.startswith("```"):
            text = re.sub(r'^```(?:json)?\s*', '', text)
            text = re.sub(r'\s*```$', '', text)

        result = json.loads(text)
        return {
            "correct": result.get("correct", False),
            "score_ratio": result.get("score_ratio", 0.0),
            "analysis": result.get("analysis", ""),
            "error_reason": result.get("error_reason", ""),
            "improvement": result.get("improvement", ""),
            "child_thinking": result.get("child_thinking", ""),
            "example": result.get("example", ""),
        }

    except Exception as e:
        logger.error(f"AI深度批改异常 ({question.get('id')}): {e}")
        return {
            "correct": False,
            "score_ratio": 0.0,
            "analysis": f"正确答案: {question.get('correct_answer', '')}",
            "error_reason": f"批改系统异常，请重试",
            "improvement": "",
            "child_thinking": "",
            "example": "",
        }


# 保留旧函数作为兼容（内部调用新函数，但只返回兼容字段）
def grade_with_ai(question: dict, student_answer: str) -> dict:
    """旧接口兼容：返回 correct / analysis / error_reason"""
    result = deep_grade_with_ai(question, student_answer, load_grading_rules())
    return {
        "correct": result["correct"],
        "analysis": result["analysis"],
        "error_reason": result["error_reason"],
    }


# ==================== 错题入库 ====================

def save_mistake_to_bitable(
    feishu_client,
    question: dict,
    student_answer: str,
    grade_result: dict,
    message_date: str,
    image_key: str = "",
) -> bool:
    """将错题存入飞书多维表格错题本（v2：支持分级评分和思维分析）"""
    if grade_result.get("correct") and grade_result.get("score_ratio", 0) >= 1.0:
        return True  # 全对不录入

    from feishu_api import bitable_add_record

    now_str = datetime.now().strftime("%Y-%m-%d %H:%M")
    score_ratio = grade_result.get("score_ratio", 0)
    status_map = {0.0: "新错题", 0.5: "半对题", 1.0: "已掌握"}
    status = status_map.get(score_ratio, "新错题")

    fields = {
        "日期": message_date,
        "科目": "数学" if question.get("id", "").startswith("M") else "英语",
        "题号": question.get("id", ""),
        "题型": question.get("type", ""),
        "题目内容": question.get("content", "")[:2000],
        "孩子答案": str(student_answer)[:1000],
        "正确答案": str(question.get("correct_answer", ""))[:1000],
        "错因分析": grade_result.get("error_reason", "")[:2000],
        "知识点": question.get("knowledge_point", ""),
        "错误次数": 1,
        "状态": status,
        "来源": "长连接批改",
        "是否已同步": False,
        "录入时间": _date_to_timestamp(now_str),
        "来源图片": image_key,
        "得分比例": score_ratio,
        "思维分析": grade_result.get("child_thinking", "")[:1000],
        "改进建议": grade_result.get("improvement", "")[:1000],
        "案例示范": grade_result.get("example", "")[:1000],
    }

    rid = bitable_add_record(feishu_client, BITABLE_APP_TOKEN, BITABLE_MISTAKE_TABLE_ID, fields)
    if rid:
        logger.info(f"错题已入库: {question.get('id')} -> 得分率{score_ratio} -> {question.get('knowledge_point')}")
        return True
    return False


def _date_to_timestamp(date_str: str, fmt: str = "%Y-%m-%d %H:%M") -> int:
    try:
        dt = datetime.strptime(date_str, fmt)
        return int(dt.timestamp() * 1000)
    except ValueError:
        return int(datetime.now().timestamp() * 1000)


# ==================== 修改建议处理 v2 ====================

def detect_modification_suggestion(text: str) -> bool:
    """检测消息是否为批改规则修改建议"""
    text_clean = text.strip()
    prefixes = [
        "调整", "修改", "新增规则", "添加规则", "删除规则", "停用规则",
        "批改规则", "调整规则", "修改批改", "规则调整",
        "建议调整", "建议修改",
    ]
    # 消息以这些关键词开头，且不是纯数字/答案格式
    is_prefix = any(text_clean.startswith(p) for p in prefixes)
    # 排除明显的答案提交（包含 |=| 格式或纯数值串）
    is_answer = (
        "=" in text_clean[:30]
        or re.fullmatch(r'[\d\s,/，、.]+', text_clean)
        or "|" in text_clean[:10]
    )
    return is_prefix and not is_answer


def process_modification_suggestion(text: str) -> dict:
    """
    使用 LLM 解析家长的修改建议，提取规则并保存

    支持的自然语言示例：
    - "调整：翻译题只要意思对就可以，不用完全一样"
    - "修改：数学应用题答案写成中文也算对"
    - "新增规则：英语单词大小写错误不扣分，标半对即可"
    - "删除规则：rule_20260518_1"

    Returns: {"success": bool, "message": str, "action": str}
    """
    if not DEEPSEEK_API_KEY:
        return {
            "success": False,
            "message": "DeepSeek API 未配置，无法处理修改建议。请在 JumpServer 上手动编辑 grading_rules.json",
            "action": "failed",
        }

    # 先检查是否是删除操作
    delete_match = re.search(r'(?:删除|停用)(?:规则)?[：:\s]*(\S+)', text)
    if delete_match:
        rule_id = delete_match.group(1).strip()
        result = delete_grading_rule(rule_id)
        result["action"] = "deleted" if result["success"] else "failed"
        return result

    # 让 AI 解析修改建议
    rules = load_grading_rules(force_reload=True)
    rules_summary = "\n".join([f"  - [{r.get('id')}] ({r.get('subject')}/{r.get('type')}): {r.get('rule')[:80]}" for r in rules])

    prompt = f"""你是一个批改规则管理助手。家长发来了一条修改建议，请你解析并提取规则。

=== 当前已有规则 ===
{rules_summary if rules_summary else "（暂无规则）"}

=== 家长的修改建议 ===
{text}

请分析这条建议，提取：
1. 操作类型：add（新增规则）/ modify（修改已有规则）/ delete（删除规则）
2. 适用科目：数学 / 英语 / all（全部）
3. 规则类型：general（通用）/ scoring（评分）/ spelling（拼写）/ calculation（计算）/ translation（翻译）/ application（应用题）/ feedback（反馈要求）
4. 规则文本：用清晰的一句话描述规则

严格按JSON格式输出：
{{
  "action": "add/modify/delete",
  "subject": "数学/英语/all",
  "type": "规则类型",
  "rule": "规则文本（一句话清晰描述）",
  "target_rule_id": "如果要修改或删除，指定目标规则ID，否则null",
  "explanation": "对这条建议的理解说明（30字以内）"
}}"""

    try:
        client = OpenAI(api_key=DEEPSEEK_API_KEY, base_url=DEEPSEEK_BASE_URL)
        resp = client.chat.completions.create(
            model=DEEPSEEK_MODEL,
            messages=[
                {"role": "system", "content": "你是批改规则管理助手。只输出JSON。"},
                {"role": "user", "content": prompt},
            ],
            temperature=0.0,
            max_tokens=500,
        )
        ai_text = resp.choices[0].message.content.strip()
        if ai_text.startswith("```"):
            ai_text = re.sub(r'^```(?:json)?\s*', '', ai_text)
            ai_text = re.sub(r'\s*```$', '', ai_text)

        parsed = json.loads(ai_text)
        action = parsed.get("action", "add")
        subject = parsed.get("subject", "all")
        rule_type = parsed.get("type", "general")
        rule_text = parsed.get("rule", text)
        explanation = parsed.get("explanation", "")

        if action == "add":
            result = add_grading_rule(subject, rule_type, rule_text, source="feishu_feedback")
        elif action == "modify":
            target_id = parsed.get("target_rule_id")
            if target_id:
                delete_grading_rule(target_id)
            result = add_grading_rule(subject, rule_type, rule_text, source="feishu_feedback")
        elif action == "delete":
            target_id = parsed.get("target_rule_id")
            if target_id:
                result = delete_grading_rule(target_id)
            else:
                result = {"success": False, "message": "未指定要删除的规则ID，请提供规则ID"}
        else:
            result = {"success": False, "message": f"未知操作: {action}"}

        result["action"] = action
        result["explanation"] = explanation
        return result

    except Exception as e:
        logger.error(f"解析修改建议失败: {e}")
        return {
            "success": False,
            "message": f"无法解析修改建议: {str(e)[:80]}。请尝试用更明确的语言，如「调整：翻译题意思对即可」",
            "action": "failed",
        }


# ==================== 主批改流程 v2 ====================

def grade_submission(
    feishu_client,
    message_text: str,
    message_date: str,
    image_key: str = "",
) -> dict:
    """
    完整批改流程 v2
    ===============
    1. 从 Bitable 读取当日题目
    2. LLM 智能解析答案（非标准格式也能处理）
    3. 逐题 LLM 深度批改（含规则注入 + 分级评分）
    4. 错题入库
    5. 返回批改结果
    """
    from feishu_api import bitable_list_records

    # Step 1: 读取当日题目
    today = message_date or datetime.now().strftime("%Y-%m-%d")
    filter_str = f'CurrentValue.[日期] = "{today}"'
    records = bitable_list_records(feishu_client, BITABLE_APP_TOKEN, BITABLE_DAILY_TABLE_ID, filter_str)

    if not records:
        return {
            "success": False,
            "summary": f"📭 {today} 还没有题目记录。请确保每日出题已推送。",
            "details": [],
        }

    # 转换为题目列表
    questions = []
    for rec in records:
        f = rec.fields or {}
        questions.append({
            "id": f.get("题号", ""),
            "num": int((f.get("题号", "0") or "0").lstrip("MEme") or 0),
            "type": f.get("题型", ""),
            "content": f.get("题目内容", ""),
            "correct_answer": f.get("正确答案", ""),
            "knowledge_point": f.get("知识点", ""),
            "score": int(f.get("分值", 0) or 0),
            "subject": f.get("科目", ""),
        })

    # 排序：数学在前，英语在后；同一科目按题号排序
    questions.sort(key=lambda q: (0 if q["subject"] == "数学" else 1, q["num"]))

    # Step 2: LLM 智能解析答案
    logger.info(f"[批改v2] 开始AI解析答案，共{len(questions)}道题")
    answers = parse_answers_with_ai(message_text, questions)
    logger.info(f"[批改v2] 解析结果: {json.dumps(answers, ensure_ascii=False)[:200]}")

    # Step 3: 加载规则 + 逐题批改
    rules = load_grading_rules()
    results = []
    total_score = 0
    earned_score = 0.0  # 改为浮点数，支持半分
    correct_count = 0
    partial_count = 0
    wrong_count = 0

    for q in questions:
        q_id = q["id"]
        student_answer = answers.get(q_id, "")

        if not student_answer or student_answer == "未作答":
            results.append({
                "question": q,
                "student_answer": "（未作答）",
                "correct": False,
                "score_ratio": 0.0,
                "analysis": "未提交答案",
                "error_reason": "未作答",
                "improvement": "请尝试完成这道题目",
                "child_thinking": "",
            })
            wrong_count += 1
            total_score += q.get("score", 0)
            continue

        # LLM 深度批改
        grade = deep_grade_with_ai(q, student_answer, rules)
        score = q.get("score", 0)
        total_score += score

        ratio = grade.get("score_ratio", 0)
        earned = score * ratio
        earned_score += earned

        if ratio >= 1.0:
            correct_count += 1
        elif ratio > 0:
            partial_count += 1
        else:
            wrong_count += 1

        # 错题/半对题入库
        if ratio < 1.0:
            save_mistake_to_bitable(feishu_client, q, student_answer, grade, today, image_key)

        results.append({
            "question": q,
            "student_answer": student_answer,
            **grade,
        })

    # Step 4: 构建批改结果
    pass_rate = round(earned_score / total_score * 100, 1) if total_score > 0 else 0

    return {
        "success": True,
        "total_score": total_score,
        "earned_score": round(earned_score, 1),
        "correct_count": correct_count,
        "partial_count": partial_count,
        "wrong_count": wrong_count,
        "pass_rate": pass_rate,
        "summary": _build_summary_v2(total_score, earned_score, correct_count, partial_count, wrong_count, pass_rate),
        "details": results,
    }


# ==================== 结果格式化 v2 ====================

def _build_summary_v2(total, earned, correct, partial, wrong, rate):
    """v2 总结语（含半对统计）"""
    if rate >= 90:
        emoji, comment = "🎉", "太棒了！继续保持！"
    elif rate >= 70:
        emoji, comment = "👍", "不错，再细心一点会更好！"
    elif rate >= 50:
        emoji, comment = "💪", "还需要多多练习，加油！"
    else:
        emoji, comment = "📚", "别灰心，我们一起看看哪里可以改进。"

    parts = [
        "━━━━━━━━━━━━━━━",
        f"📊 今日成绩：{earned}/{total} 分（得分率 {rate}%）",
        f"✅ 全对：{correct} 道",
    ]
    if partial > 0:
        parts.append(f"🔶 半对：{partial} 道")
    parts.append(f"❌ 错误：{wrong} 道")
    parts.append(f"{emoji} {comment}")
    return "\n".join(parts)


# 保留旧函数兼容
def _build_summary(total, earned, correct, wrong, rate):
    return _build_summary_v2(total, earned, correct, 0, wrong, rate)


def format_grading_card(result: dict) -> tuple:
    """格式化批改结果为 (标题, 卡片内容) - v2增强版"""
    title = f"📝 批改结果 · {datetime.now().strftime('%m月%d日')}"

    if not result.get("success"):
        return title, result.get("summary", "批改遇到问题")

    content = result["summary"] + "\n\n"

    for i, item in enumerate(result["details"], 1):
        q = item["question"]
        ratio = item.get("score_ratio", 0)
        if ratio >= 1.0:
            mark = "✅"
        elif ratio > 0:
            mark = "🔶"
        else:
            mark = "❌"
        subject_icon = "📐" if q.get("subject") == "数学" else "📘"

        content += f"**{subject_icon} 第{i}题【{q.get('type','')}】({q.get('score',0)}分) {mark}**\n"
        content += f"📝 题目：{q.get('content','')[:100]}...\n"
        content += f"✏️ 孩子答案：{item.get('student_answer','')}\n"

        if ratio < 1.0:
            content += f"✅ 正确答案：{q.get('correct_answer','')}\n"

        if item.get("analysis"):
            content += f"💡 解析：{item.get('analysis','')}\n"

        if item.get("child_thinking"):
            content += f"🧠 思路分析：{item.get('child_thinking','')}\n"

        if item.get("error_reason") and ratio < 1.0:
            content += f"⚠️ 错因：{item.get('error_reason','')}\n"

        if item.get("improvement") and ratio < 1.0:
            content += f"📝 改进建议：{item.get('improvement','')}\n"

        if item.get("example") and ratio < 1.0:
            content += f"🌟 案例示范：{item.get('example','')}\n"

        if ratio < 1.0:
            content += f"📒 已录入错题本 ✓\n"

        content += "\n"

    content += "---\n"
    content += "> 🐱 小肥猫学习·智能批改 v2.1\n"
    content += "> 📋 英语=KET标准 | 数学=小学考试标准\n"
    content += "> 💡 回复「调整：XXX」即可修改批改规则"

    return title, content


# ==================== 指令识别 ====================

def is_command(text: str) -> bool:
    """判断消息是否为指令（非答案提交）"""
    text_lower = text.lower().strip()
    commands = [
        "增加需求如下", "新需求", "查看错题本", "错题查询",
        "生成今日练习", "出今天的题", "暂停推送", "停止推送",
        "恢复推送", "开始推送", "录错题", "加入错题本",
        "记录错题", "手动录错",
        "标准答案", "答案确认",
        "查看规则", "规则列表", "所有规则",
    ]
    return any(cmd in text_lower for cmd in commands)


# ==================== 工具函数 ====================

def normalize_answer(ans: str) -> str:
    """标准化答案（忽略大小写、空格差异）- 保留供外部使用"""
    return ans.strip().lower().replace(" ", "")
