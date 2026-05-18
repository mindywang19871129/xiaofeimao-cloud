"""
小肥猫学习 - 批改核心逻辑
=========================
从题目解析 → AI批改 → 错题入库 → 结果格式化
复用自 EdgeOne Pages 云函数版本，适配 WebSocket 模式。
"""

import os
import re
import json
import logging
from datetime import datetime

from openai import OpenAI

logger = logging.getLogger("grading")

# ==================== 配置（从环境变量读取） ====================

DEEPSEEK_API_KEY = os.environ.get("DEEPSEEK_API_KEY", "")
DEEPSEEK_BASE_URL = "https://api.deepseek.com"
DEEPSEEK_MODEL = "deepseek-chat"

BITABLE_APP_TOKEN = os.environ.get("BITABLE_APP_TOKEN", "")
BITABLE_DAILY_TABLE_ID = os.environ.get("BITABLE_DAILY_TABLE_ID", "")
BITABLE_MISTAKE_TABLE_ID = os.environ.get("BITABLE_MISTAKE_TABLE_ID", "")


# ==================== 答案解析 ====================

def parse_answers(message_text: str, questions: list) -> dict:
    """
    解析家长提交的答案
    支持格式：
      |1|83| |2|44| |3|63,22|
      M1=83 M2=44 E1=forget/arrive/plan
      83 44 63,22 forget arrive plan（按顺序匹配）
    """
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


def normalize_answer(ans: str) -> str:
    """标准化答案（忽略大小写、空格差异）"""
    return ans.strip().lower().replace(" ", "")


# ==================== DeepSeek 批改 ====================

def grade_with_ai(question: dict, student_answer: str) -> dict:
    """
    AI 批改单道题
    Returns: {"correct": bool, "analysis": str, "error_reason": str}
    """
    correct_answer = str(question.get("correct_answer", ""))
    q_type = question.get("type", "")

    # 精确匹配
    if normalize_answer(student_answer) == normalize_answer(correct_answer):
        return {"correct": True, "analysis": "答案完全正确", "error_reason": ""}

    # 数学数字模糊匹配（忽略单位）
    if q_type in ["口算速算", "竖式计算", "计算", "脱式计算", "填空"]:
        s_nums = re.findall(r'-?\d+\.?\d*', student_answer)
        c_nums = re.findall(r'-?\d+\.?\d*', correct_answer)
        if s_nums == c_nums:
            return {"correct": True, "analysis": "数值正确（可能有细微格式差异）", "error_reason": ""}

    # AI 深度批改
    return _ai_deep_grade(question, student_answer, correct_answer)


def _ai_deep_grade(question: dict, student_answer: str, correct_answer: str) -> dict:
    """DeepSeek 深度批改（准确判断 + 错因分析）"""
    if not DEEPSEEK_API_KEY:
        return {
            "correct": False,
            "analysis": f"正确答案应为: {correct_answer}",
            "error_reason": "DeepSeek API未配置，无法智能分析错因",
        }

    try:
        client = OpenAI(api_key=DEEPSEEK_API_KEY, base_url=DEEPSEEK_BASE_URL)

        prompt = f"""你是一个三年级数学+英语KET批改助手。请批改下面这道题：

题目内容：{question.get('content', '')}
题目类型：{question.get('type', '')}
正确答案：{correct_answer}
学生答案：{student_answer}
知识点：{question.get('knowledge_point', '')}

请判断对错并分析。用JSON格式回复：
{{
  "correct": true/false,
  "analysis": "对在何处或详细的解题步骤（50字以内）",
  "error_reason": "如果错误，具体说明错在哪里（50字以内）"
}}"""

        resp = client.chat.completions.create(
            model=DEEPSEEK_MODEL,
            messages=[
                {"role": "system", "content": "你是三年级批改助手。只输出JSON，不要说其他话。"},
                {"role": "user", "content": prompt},
            ],
            temperature=0.1,
            max_tokens=500,
        )

        text = resp.choices[0].message.content.strip()

        if text.startswith("```"):
            text = re.sub(r'^```(?:json)?\s*', '', text)
            text = re.sub(r'```\s*$', '', text)

        result = json.loads(text)
        return {
            "correct": result.get("correct", False),
            "analysis": result.get("analysis", "AI分析结果"),
            "error_reason": result.get("error_reason", ""),
        }

    except Exception as e:
        logger.error(f"DeepSeek批改异常: {e}")
        return {
            "correct": False,
            "analysis": f"正确答案: {correct_answer}",
            "error_reason": f"批改系统异常: {str(e)[:50]}",
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
    """将错题存入飞书多维表格错题本（支持图片来源追溯）"""
    if grade_result["correct"]:
        return True

    from feishu_api import bitable_add_record

    now_str = datetime.now().strftime("%Y-%m-%d %H:%M")
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
        "状态": "新错题",
        "来源": "长连接批改",
        "是否已同步": False,
        "录入时间": _date_to_timestamp(now_str),
        "来源图片": image_key,  # 图片批改来源追溯
    }

    rid = bitable_add_record(feishu_client, BITABLE_APP_TOKEN, BITABLE_MISTAKE_TABLE_ID, fields)
    if rid:
        logger.info(f"错题已入库: {question.get('id')} -> {question.get('knowledge_point')}")
        return True
    return False


def _date_to_timestamp(date_str: str, fmt: str = "%Y-%m-%d %H:%M") -> int:
    """日期字符串 -> 毫秒时间戳"""
    try:
        dt = datetime.strptime(date_str, fmt)
        return int(dt.timestamp() * 1000)
    except ValueError:
        return int(datetime.now().timestamp() * 1000)


# ==================== 主批改流程 ====================

def grade_submission(
    feishu_client,
    message_text: str,
    message_date: str,
    image_key: str = "",
) -> dict:
    """
    完整批改流程
    1. 从 Bitable 读取当日题目
    2. 逐题批改
    3. 错题入库（支持图片来源追溯）
    4. 返回批改结果
    """
    from feishu_api import bitable_list_records

    # Step 1: 读取当日题目
    today = message_date or datetime.now().strftime("%Y-%m-%d")
    filter_str = f'CurrentValue.[日期] = "{today}"'
    records = bitable_list_records(feishu_client, BITABLE_APP_TOKEN, BITABLE_DAILY_TABLE_ID, filter_str)

    if not records:
        return {
            "success": False,
            "summary": f"📭 {today} 还没有题目记录。请确保 Mac 端已完成今日出题推送。",
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

    # Step 2: 解析答案
    answers = parse_answers(message_text, questions)
    logger.info(f"解析到 {len(answers)} 个答案: {answers}")

    # Step 3: 逐题批改
    results = []
    total_score = 0
    earned_score = 0
    correct_count = 0
    wrong_count = 0

    for q in questions:
        q_id = q["id"]
        student_answer = answers.get(q_id, answers.get(q["num"], ""))

        if not student_answer:
            results.append({
                "question": q,
                "student_answer": "（未作答）",
                "correct": False,
                "analysis": "未提交答案",
                "error_reason": "未作答",
            })
            wrong_count += 1
            total_score += q.get("score", 0)
            continue

        grade = grade_with_ai(q, student_answer)
        total_score += q.get("score", 0)

        if grade["correct"]:
            earned_score += q.get("score", 0)
            correct_count += 1
        else:
            wrong_count += 1
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
        "earned_score": earned_score,
        "correct_count": correct_count,
        "wrong_count": wrong_count,
        "pass_rate": pass_rate,
        "summary": _build_summary(total_score, earned_score, correct_count, wrong_count, pass_rate),
        "details": results,
    }


# ==================== 结果格式化 ====================

def _build_summary(total, earned, correct, wrong, rate):
    """构建总结语"""
    if rate >= 90:
        emoji, comment = "🎉", "太棒了！继续保持！"
    elif rate >= 70:
        emoji, comment = "👍", "不错，再细心一点会更好！"
    elif rate >= 50:
        emoji, comment = "💪", "还需要多多练习，加油！"
    else:
        emoji, comment = "📚", "别灰心，我们一起看看哪里可以改进。"

    return (
        f"━━━━━━━━━━━━━━━\n"
        f"📊 今日成绩：{earned}/{total} 分（得分率 {rate}%）\n"
        f"✅ 正确：{correct} 道 | ❌ 错误：{wrong} 道\n"
        f"{emoji} {comment}\n"
    )


def format_grading_card(result: dict) -> tuple:
    """格式化批改结果为 (标题, 卡片内容)"""
    title = f"📝 批改结果 · {datetime.now().strftime('%m月%d日')}"

    if not result.get("success"):
        return title, result.get("summary", "批改遇到问题")

    content = result["summary"] + "\n\n"

    for i, item in enumerate(result["details"], 1):
        q = item["question"]
        mark = "✅" if item["correct"] else "❌"
        subject_icon = "📐" if q.get("subject") == "数学" else "📘"

        content += f"**{subject_icon} 第{i}题【{q.get('type','')}】({q.get('score',0)}分) {mark}**\n"
        content += f"📝 题目：{q.get('content','')[:100]}...\n"
        content += f"✏️ 孩子答案：{item.get('student_answer','')}\n"

        if not item["correct"]:
            content += f"✅ 正确答案：{q.get('correct_answer','')}\n"
            content += f"💡 解析：{item.get('analysis','')}\n"
            if item.get("error_reason"):
                content += f"⚠️ 错因：{item.get('error_reason','')}\n"
                content += f"📒 已录入错题本 ✓\n"

        content += "\n"

    content += "---\n"
    content += "> 🐱 小肥猫学习·长连接批改\n"
    content += "> 💡 错题已自动记录，下次练习会针对性复习！"

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
        # ===== 标准答案确认（非学生提交）=====
        "标准答案", "答案确认",
    ]
    return any(cmd in text_lower for cmd in commands)
