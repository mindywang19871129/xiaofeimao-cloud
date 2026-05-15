#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
小肥猫学习 - 飞书机器人后端服务
架构：飞书长连接接收消息 → DeepSeek API 批改 → 飞书 API 回复

启动方式：python3 bot_server.py
依赖：pip3 install lark-oapi openai
"""

import json
import os
import sys
import re
import time
import logging
import threading
from pathlib import Path
from datetime import datetime

# 飞书 SDK（提前导入，确保全局可用）
import lark_oapi
from lark_oapi import EventDispatcherHandler

# HTTP 请求库
import requests

# ==================== 配置区 ====================

# 飞书应用配置
FEISHU_APP_ID = "cli_aa8f8d25a925dbea"
FEISHU_APP_SECRET = "9vyD11qA4jIxn3PCQB1jnfvTXMXs2Rve"

# DeepSeek API 配置（OpenAI 兼容接口）
DEEPSEEK_API_KEY = os.environ.get("DEEPSEEK_API_KEY", "sk-f5d41971d21d46ffbdd4e1d7af4a093c")  # DeepSeek API Key
DEEPSEEK_BASE_URL = "https://api.deepseek.com"
DEEPSEEK_MODEL = "deepseek-chat"

# 工作目录（所有数据文件都在这里）
WORK_DIR = Path(__file__).parent.resolve()
SYSTEM_PROMPT_FILE = WORK_DIR / "system_prompt.md"
DAILY_QUESTIONS_FILE = WORK_DIR / "daily_questions.json"
MISTAKE_BOOK_FILE = WORK_DIR / "mistake_book.json"

# 日志配置
LOG_DIR = WORK_DIR / ".logs"
LOG_FILE = LOG_DIR / "bot_server.log"

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
logger = logging.getLogger("xiaofeimao")


# ==================== 文件读取工具 ====================

def read_file_safe(filepath: Path, default="") -> str:
    """安全读取文件，不存在返回默认值"""
    try:
        if filepath.exists():
            return filepath.read_text(encoding="utf-8")
        else:
            logger.warning(f"文件不存在: {filepath}")
            return default
    except Exception as e:
        logger.error(f"读取文件失败 {filepath}: {e}")
        return default


def write_file_safe(filepath: Path, content: str):
    """安全写入文件"""
    try:
        filepath.write_text(content, encoding="utf-8")
        logger.info(f"文件已写入: {filepath}")
    except Exception as e:
        logger.error(f"写入文件失败 {filepath}: {e}")


def read_json_safe(filepath: Path, default=None) -> dict:
    """安全读取 JSON 文件"""
    if default is None:
        default = {}
    try:
        if filepath.exists():
            text = filepath.read_text(encoding="utf-8")
            if text.strip():
                return json.loads(text)
        return default
    except Exception as e:
        logger.error(f"读取JSON失败 {filepath}: {e}")
        return default


def write_json_safe(filepath: Path, data: dict):
    """安全写入 JSON 文件"""
    try:
        filepath.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        logger.info(f"JSON已写入: {filepath}")
    except Exception as e:
        logger.error(f"写入JSON失败 {filepath}: {e}")


# ==================== AI 调用 (DeepSeek) ====================

def call_deepseek(system_prompt: str, user_message: str) -> str:
    """
    调用 DeepSeek API 进行对话
    system_prompt: 系统提示词（包含规则+题目）
    user_message: 用户消息（家长提交的答案）
    返回: AI 的回复文本
    """
    from openai import OpenAI

    if not DEEPSEEK_API_KEY:
        logger.error("DeepSeek API Key 未设置！请设置环境变量 DEEPSEEK_API_KEY 或在代码中填入")
        return "⚠️ AI 服务未配置，请联系管理员设置 DeepSeek API Key。"

    client = OpenAI(
        api_key=DEEPSEEK_API_KEY,
        base_url=DEEPSEEK_BASE_URL
    )

    try:
        logger.info(f"调用 DeepSeek API... (消息长度: {len(user_message)})")
        
        response = client.chat.completions.create(
            model=DEEPSEEK_MODEL,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_message}
            ],
            temperature=0.3,  # 低温度保证批改稳定
            max_tokens=2000
        )
        
        reply = response.choices[0].message.content
        logger.info(f"DeepSeek 回复长度: {len(reply)}")
        return reply
        
    except Exception as e:
        logger.error(f"DeepSeek API 调用失败: {e}")
        return f"⚠️ AI 服务暂时不可用: {str(e)}"


# ==================== 构建完整 Prompt ====================

def build_full_prompt() -> str:
    """
    构建完整的 System Prompt：
    1. 基础 System Prompt（身份 + 规则 + 批改流程）
    2. 当日题目数据（从 daily_questions.json 读取）
    3. 错题本摘要（可选，用于混入复习题提示）
    """
    # Part 1: 基础规则
    base_prompt = read_file_safe(SYSTEM_PROMPT_FILE)
    if not base_prompt:
        logger.error("system_prompt.md 为空或不存在！")
        return "你是小肥猫学习，一个批改作业的机器人。收到答案后立即批改。"

    # Part 2: 当日题目
    daily_data = read_json_safe(DAILY_QUESTIONS_FILE)
    
    questions_section = "\n\n## 📋 当天题目数据（从 daily_questions.json 加载）\n\n"
    if daily_data and "date" in daily_data:
        questions_section += f"**日期**: {daily_data['date']}\n"
        questions_section += f"**状态**: {daily_data.get('status', 'unknown')}\n"
        questions_section += f"**总分**: {daily_data.get('total_score', 0)} 分\n\n"
        
        # 数学题目
        if "math" in daily_data:
            math_data = daily_data["math"]
            questions_section += f"### 📐 {math_data.get('subject', '数学')}（共{math_data.get('total_score', 0)}分，{math_data.get('count', 0)}道题）\n\n"
            for q in math_data.get("questions", []):
                questions_section += f"**第{q['num']}题（{q['type']}，{q['score']}分）**: {q['content']}\n"
                questions_section += f"  - 正确答案: **{q['correct_answer']}**\n"
                questions_section += f"  - 知识点: {q.get('knowledge_point', '')}\n"
                questions_section += f"  - 解析: {q.get('explanation', '')}\n\n"
        
        # 英语题目
        if "english" in daily_data:
            eng_data = daily_data["english"]
            questions_section += f"### 📘 {eng_data.get('subject', '英语')}（共{eng_data.get('total_score', 0)}分，{eng_data.get('count', 0)}道题）\n\n"
            for q in eng_data.get("questions", []):
                questions_section += f"**第{q['num']}题（{q['type']}，{q['score']}分）**: {q['content']}\n"
                questions_section += f"  - 正确答案: **{q['correct_answer']}**\n"
                questions_section += f"  - 知识点: {q.get('knowledge_point', '')}\n"
                questions_section += f"  - 解析: {q.get('explanation', '')}\n"
                if q.get("scoring_criteria"):
                    questions_section += f"  - 评分标准: {' / '.join(q['scoring_criteria'])}\n"
                questions_section += "\n"
    else:
        questions_section += "⚠️ 今天还没有生成题目数据。\n"

    # Part 3: 错题本摘要（简要）
    mistake_book = read_json_safe(MISTAKE_BOOK_FILE)
    if mistake_book and mistake_book.get("mistakes"):
        questions_section += "\n### 📒 错题本概览\n"
        for m in mistake_book["mistakes"][-5:]:  # 最近5条
            questions_section += f"- [{m.get('date','')}] {m.get('subject','')} 第{m.get('question_id','')}: {m.get('question_content','')} → 答'{m.get('student_answer','')}' 应为'{m.get('correct_answer','')}'\n"
    else:
        questions_section += "\n### 📒 错题本：暂无记录（全对！🎉）\n"

    full_prompt = base_prompt + questions_section
    
    logger.info(f"完整 Prompt 构建完成，总长度: {len(full_prompt)} 字符")
    return full_prompt


# ==================== 消息处理核心逻辑 ====================

def process_message(user_text: str) -> str:
    """
    处理家长发来的消息：
    1. 判断是否是特殊指令
    2. 如果是答案 → 构建 prompt → 调用 DeepSeek → 返回批改结果
    """
    user_text = user_text.strip()
    logger.info(f"收到消息: '{user_text[:100]}{'...' if len(user_text)>100 else ''}'")

    # ===== 特殊指令判断 =====
    special_commands = {
        "增加需求如下": "REQ_CHANGE",
        "新需求": "REQ_CHANGE",
        "查看错题本": "VIEW_MISTAKES",
        "错题查询": "VIEW_MISTAKES",
        "生成今日练习": "GEN_QUESTIONS",
        "出今天的题": "GEN_QUESTIONS",
        "暂停推送": "PAUSE_PUSH",
        "停止推送": "PAUSE_PUSH",
        "恢复推送": "RESUME_PUSH",
        "开始推送": "RESUME_PUSH",
        # ===== 新增：自由录入错题本 =====
        "录错题": "ADD_MISTAKE",
        "加入错题本": "ADD_MISTAKE",
        "记录错题": "ADD_MISTAKE",
        "手动录错": "ADD_MISTAKE",
    }

    for keyword, cmd_type in special_commands.items():
        if keyword in user_text:
            logger.info(f"识别到特殊指令: {cmd_type} ({keyword})")
            return handle_special_command(cmd_type, user_text)

    # ===== 默认：作为答案处理 =====
    logger.info("→ 默认按答案提交处理")

    # 构建完整 Prompt（规则 + 当天题目 + 错题本）
    full_prompt = build_full_prompt()

    # 调用 DeepSeek 批改
    reply = call_deepseek(full_prompt, user_text)

    # 尝试提取错题并更新错题本（异步，不阻塞回复）
    threading.Thread(target=_try_update_mistake_book, args=(reply,), daemon=True).start()

    return reply


def handle_special_command(cmd_type: str, original_msg: str) -> str:
    """处理特殊指令"""
    if cmd_type == "REQ_CHANGE":
        new_req = original_msg.replace("增加需求如下", "").replace("新需求", "").strip()
        # 记录到 memory
        log_entry = f"\n## 规则变更请求 ({datetime.now().strftime('%Y-%m-%d %H:%M')})\n{new_req}\n"
        memory_file = WORK_DIR / ".workbuddy" / "memory" / f"{datetime.now().strftime('%Y-%m-%d')}.md"
        memory_file.parent.mkdir(parents=True, exist_ok=True)
        if memory_file.exists():
            existing = memory_file.read_text(encoding="utf-8")
            memory_file.write_text(existing + log_entry, encoding="utf-8")
        else:
            memory_file.write_text(f"# {datetime.now().strftime('%Y-%m-%d')} 工作记录\n{log_entry}", encoding="utf-8")
        
        return f"✅ 收到新的需求！已记录：\n{new_req}\n\n我会在 WorkBuddy 中更新规则。感谢反馈！🐱"

    elif cmd_type == "VIEW_MISTAKES":
        mistake_book = read_json_safe(MISTAKE_BOOK_FILE)
        if not mistake_book or not mistake_book.get("mistakes"):
            return "📒 **错题本是空的！**\n\n宝贝太棒了！目前没有错题记录，继续加油！🎉"
        
        reply = "📒 **错题本记录**\n\n"
        for i, m in enumerate(mistake_book["mistakes"], 1):
            status_icon = "⏳" if m.get("status") != "mastered" else "✅"
            reply += f"{i}. {status_icon} [{m.get('date','')}] {m.get('subject','')} - {m.get('question_content','')}\n"
            reply += f"   ❌ 孩子答: **{m.get('student_answer','')}** | ✅ 正确: **{m.get('correct_answer','')}**\n"
            reply += f"   💡 错因: {m.get('error_reason','')}\n"
            reply += f"   📚 知识点: {m.get('knowledge_point','')}\n"
            reply += f"   🔄 错误次数: {m.get('error_count', 0)} 次 | 下次复习: {m.get('next_review_date', '待定')}\n\n"
        
        stats = mistake_book.get("stats", {})
        reply += f"---\n📊 总计: {len(mistake_book['mistakes'])} 条错题"
        return reply

    elif cmd_type == "GEN_QUESTIONS":
        return "📝 正在为你生成今日练习题...（此功能需要调用出题模块，稍后支持自动触发）"

    elif cmd_type in ("PAUSE_PUSH", "RESUME_PUSH"):
        action = "暂停" if cmd_type == "PAUSE_PUSH" else "恢复"
        return f"✅ 已收到{action}推送请求！（此功能需要更新自动化任务状态）"

    elif cmd_type == "ADD_MISTAKE":
        # ===== 新增：自由录入错题本功能 =====
        return handle_add_mistake(original_msg)

    return "收到指令，正在处理..."


def _try_update_mistake_book(ai_reply: str):
    """
    从 AI 批改回复中提取错题信息并更新错题本
    策略：
    1. 用正则快速匹配明显的 ❌ 错误标记段落
    2. 如果正则匹配不够，调用 DeepSeek 做结构化提取
    3. 写入 mistake_book.json（遵循艾宾浩斯间隔复习）
    """
    try:
        # ===== Step 1: 快速检查是否有错题 =====
        if "❌" not in ai_reply and "错误" not in ai_reply:
            logger.info("✅ 批改回复中没有发现错题标记，跳过错题录入")
            return

        # ===== Step 2: 用 AI 结构化提取错题 =====
        extraction_prompt = f"""你是一个数据提取助手。从下面的AI批改回复中，提取所有错题信息。

## 提取规则
1. 只提取**答错了的题目**（有❌标记或明确说"错误"的）
2. 每条错题必须包含以下字段
3. 如果某个字段在回复中找不到，用空字符串""
4. 输出纯JSON数组，不要markdown包裹

## 输出 JSON 格式
[
  {{
    "question_id": "题号如M1/E2",
    "subject": "数学 or 英语",
    "question_type": "题型",
    "question_content": "题目内容摘要",
    "student_answer": "孩子的答案",
    "correct_answer": "正确答案",
    "error_reason": "错误原因（从回复中的'错因分析'提取）",
    "knowledge_point": "知识点标签"
  }}
]

## 批改回复内容
---
{ai_reply}
---

请只输出JSON数组，如果有错题就提取，没有就输出空数组 []："""

        from openai import OpenAI
        client = OpenAI(
            api_key=DEEPSEEK_API_KEY,
            base_url=DEEPSEEK_BASE_URL
        )

        logger.info("🔍 调用 DeepSeek 提取错题...")
        response = client.chat.completions.create(
            model="deepseek-chat",
            messages=[
                {"role": "system", "content": "你是数据提取助手。只输出JSON数组，不输出任何其他文字。"},
                {"role": "user", "content": extraction_prompt}
            ],
            temperature=0.1,
            max_tokens=2000
        )

        raw_extracted = response.choices[0].message.content.strip()
        logger.info(f"提取结果长度: {len(raw_extracted)}")

        # 清洗 JSON
        import re as _re
        text = raw_extracted.strip()
        if text.startswith("```"):
            first = text.find("```")
            second = text.find("```", first + 3)
            if second > first:
                text = text[first+3:second]
                if text.startswith("json"):
                    text = text[4:].strip()
            else:
                text = _re.sub(r'^```(?:json)?\s*', '', text)
                text = _re.sub(r'```\s*$', '', text)

        mistakes_list = json.loads(text.strip())

        if not mistakes_list:
            logger.info("✅ 提取结果为空，说明可能全对或无法识别错题格式")
            return

        logger.info(f"📒 提取出 {len(mistakes_list)} 条错题")

        # ===== Step 3: 读取现有错题本 =====
        existing_book = read_json_safe(MISTAKE_BOOK_FILE, default={
            "version": "1.0",
            "created_at": "2026-05-14",
            "student_info": {
                "grade": "三年级下学期",
                "math_textbook": "北师大版2026新版三下",
                "english_exam": "KET (A2 Key)"
            },
            "mistakes": [],
            "stats": {"total_mistakes": 0, "by_subject": {"math": 0, "english": 0}, "by_error_type": {}, "resolved_count": 0},
            "review_schedule": []
        })

        today = datetime.now().strftime("%Y-%m-%d")
        new_count = 0

        for m in mistakes_list:
            qid = m.get("question_id", "unknown")
            subject = m.get("subject", "")
            kp = m.get("knowledge_point", "")

            # 检查是否已有同一知识点的活跃错题（避免重复录入完全相同的题）
            is_duplicate = False
            for existing in existing_book["mistakes"]:
                if (existing.get("question_content") == m.get("question_content") and
                    existing.get("status") != "mastered"):
                    # 已存在 → 更新错误次数
                    existing["error_count"] = existing.get("error_count", 1) + 1
                    existing["last_review_date"] = today
                    # 重新计算下次复习日期（间隔拉长）
                    next_days = _get_next_interval(existing["error_count"])
                    existing["next_review_date"] = (
                        datetime.now() + timedelta(days=next_days)
                    ).strftime("%Y-%m-%d")
                    is_duplicate = True
                    logger.info(f"  🔄 已有条目 {qid} 更新错误次数→{existing['error_count']}")
                    break

            if is_duplicate:
                continue

            # 新建错题记录
            new_record = {
                "date": today,
                "subject": subject,
                "question_id": qid,
                "question_type": m.get("question_type", ""),
                "question_content": m.get("question_content", ""),
                "student_answer": m.get("student_answer", ""),
                "correct_answer": m.get("correct_answer", ""),
                "error_reason": m.get("error_reason", ""),
                "knowledge_point": kp,
                "error_count": 1,
                "status": "new",
                "last_review_date": today,
                "next_review_date": (
                    datetime.now() + timedelta(days=1)  # 新错题1天后首次复习
                ).strftime("%Y-%m-%d"),
                "variant_questions": []
            }
            existing_book["mistakes"].append(new_record)
            new_count += 1
            logger.info(f"  ✅ 新增错题: {qid} ({subject}/{kp})")

        # 更新统计
        existing_book["stats"]["total_mistakes"] = len(existing_book["mistakes"])
        for subj in ["math", "english"]:
            existing_book["stats"]["by_subject"][subj] = sum(
                1 for m in existing_book["mistakes"] if m.get("subject") == subj
            )

        # 写回文件
        write_json_safe(MISTAKE_BOOK_FILE, existing_book)
        logger.info(f"💾 错题本已更新: +{new_count} 条, 总计 {len(existing_book['mistakes'])} 条")

    except json.JSONDecodeError as e:
        logger.warning(f"错题提取JSON解析失败（非致命）: {e}")
    except Exception as e:
        logger.error(f"错题本自动录入异常（非致命，不影响批改）: {e}", exc_info=True)


# ==================== 自由录入错题本功能 ====================

def handle_add_mistake(original_msg: str) -> str:
    """
    处理家长通过飞书手动录入错题的请求
    支持格式：
    1. 自然语言描述（如"录错题 数学 计算题 56+23=79 孩子答了72 算错进位"）
    2. 结构化格式（如"录错题|数学|乘法竖式|45×23孩子答835正确1035|十位进位漏加"）

    流程：
    1. 提取指令后面的内容
    2. 调用 AI 解析成结构化数据
    3. 写入 mistake_book.json
    4. 返回确认结果
    """
    # 提取错题内容（去掉各种前缀关键词）
    content = original_msg
    for prefix in ["录错题", "加入错题本", "记录错题", "手动录错"]:
        content = content.replace(prefix, "", 1)
    content = content.strip()

    if not content or len(content) < 5:
        return """📒 **自由录入错题本**

请在消息中告诉我错题信息，支持以下方式：

**方式1：自然语言** 📝
直接描述，例如：
> 录错题 数学 今天学校作业 一道除法竖式 84÷4 孩子商写成21 忘了中间有0 正确应该是21吗不对是21... 哦对是21但他没写0

**方式2：结构化格式** 🔧
> 录错题|数学|除法|84÷4=21(孩子答的)|正确=21|忘记商中间补0

**需要包含的信息：**
- 科目（数学/英语）
- 题目内容或大致描述
- 孩子的答案
- 正确答案（如果知道的话，不知道我可以帮你判断）
- 错因（可选，不知道也可以让我分析）

---

💡 **提示**：你也可以把学校的作业、试卷、练习册上的错题拍照后文字发给我，我会帮你录入！"""

    # 调用 AI 解析家长的输入
    logger.info(f"📒 收到自由录入错题请求，内容长度: {len(content)}")

    try:
        parsed = _ai_parse_mistake_input(content)

        if not parsed or not parsed.get("mistakes"):
            return "⚠️ 我没能从你的描述中提取出错题信息。请换个方式再试一次，或者按上面的格式重新发送~\n\n你可以这样说：\n> 录错题 数学 应用题 一个长方形长8cm宽5cm求周长 孩子答40 正确是26"

        # 将解析出的错题写入错题本
        result = _save_parsed_mistakes(parsed["mistakes"])

        # 构建回复
        reply = f"📒 **错题本已更新！**\n\n"
        reply += f"✅ 成功录入 **{result['added']}** 条错题：\n\n"

        for i, m in enumerate(result["added_records"], 1):
            subject_icon = "📐" if m.get("subject") == "数学" else "📘"
            reply += f"{i}. {subject_icon} **{m.get('subject', '?')}** | {m.get('question_content', '')}\n"
            reply += f"   ❌ 孩子答: **{m.get('student_answer', '')}**\n"
            if m.get("correct_answer"):
                reply += f"   ✅ 正确: **{m.get('correct_answer', '')}**\n"
            if m.get("error_reason"):
                reply += f"   💡 错因: {m.get('error_reason', '')}\n"
            if m.get("source") != "系统出题":
                reply += f"   📌 来源: **{m.get('source', '手动录入')}**\n"
            reply += "\n"

        if result.get("duplicates", 0) > 0:
            reply += f"🔄 已有 **{result['duplicates']}** 条相同知识点的错题（已更新错误次数）\n\n"

        total_now = result.get("total_count", "?")
        reply += f"---\n📊 错题本当前共 **{total_now}** 条记录\n"
        reply += f"🔄 下次复习时间将根据艾宾浩斯间隔自动安排！\n\n"
        reply += "> ✨ 继续发送错题我会继续录入哦～"

        return reply

    except Exception as e:
        logger.error(f"自由录入错题处理异常: {e}", exc_info=True)
        return f"⚠️ 处理错题录入时遇到问题: {str(e)}\n请稍后再试或换个格式描述错题。"


def _ai_parse_mistake_input(user_input: str) -> dict:
    """
    调用 AI 将家长的自然语言错题描述解析为结构化数据
    """
    parse_prompt = f"""你是一个错题数据提取助手。家长要通过飞书机器人手动录入孩子的错题（来自学校作业、试卷或其他来源），请从下面的文字描述中提取错题信息。

## 提取规则
1. 分析家长描述的内容，识别科目、题目、孩子的答案、正确答案、错因等
2. 如果家长没有提供正确答案，尝试根据题目计算/推断出来
3. 如果无法确定某些字段，用合理的推断值填充
4. 输出纯JSON对象，不要markdown包裹

## 家长的原始输入
---
{user_input}
---

## 输出 JSON 格式
{{
  "mistakes": [
    {{
      "subject": "数学 or 英语",
      "question_type": "题型（如：计算题/应用题/填空/选择/单词拼写/语法填空/作文等）",
      "question_content": "完整的题目内容（尽量还原原题，如果家长描述不完整就根据描述整理）",
      "student_answer": "孩子的答案",
      "correct_answer": "正确答案（如果家长说了就用家长的，没说但你能算出来的就算出来，实在不知道就留空字符串）",
      "error_reason": "错误原因（根据题目和错误答案分析）",
      "knowledge_point": "知识点标签（如：两位数退位减法 / 一般过去时不规则动词 / 面积公式应用 等）",
      "source": "学校作业 / 试卷 / 练习册 / 其他（根据描述中的线索判断）"
    }}
  ],
  "parse_notes": "简要说明你对家长输入的理解（用于日志）"
}}

请只输出JSON对象："""

    from openai import OpenAI
    client = OpenAI(
        api_key=DEEPSEEK_API_KEY,
        base_url=DEEPSEEK_BASE_URL
    )

    logger.info("🔍 调用 AI 解析自由录入的错题...")
    response = client.chat.completions.create(
        model="deepseek-chat",
        messages=[
            {"role": "system", "content": "你是错题数据提取助手。只输出JSON对象，不输出任何其他文字。"},
            {"role": "user", "content": parse_prompt}
        ],
        temperature=0.2,  # 低温度保证解析稳定准确
        max_tokens=2000
    )

    raw = response.choices[0].message.content.strip()
    logger.info(f"AI 解析结果长度: {len(raw)}")

    # 清洗 JSON（复用同样的清洗逻辑）
    text = raw.strip()
    if text.startswith("```"):
        first = text.find("```")
        second = text.find("```", first + 3)
        if second > first:
            text = text[first+3:second]
            if text.startswith("json"):
                text = text[4:].strip()
        else:
            text = re.sub(r'^```(?:json)?\s*', '', text)
            text = re.sub(r'```\s*$', '', text)

    data = json.loads(text.strip())
    return data


def _save_parsed_mistakes(mistakes_list: list) -> dict:
    """
    将 AI 解析出的错题列表写入 mistake_book.json
    返回操作结果统计
    """
    existing_book = read_json_safe(MISTAKE_BOOK_FILE, default={
        "version": "1.0",
        "created_at": "2026-05-14",
        "student_info": {
            "grade": "三年级下学期",
            "math_textbook": "北师大版2026新版三下",
            "english_exam": "KET (A2 Key)"
        },
        "mistakes": [],
        "stats": {"total_mistakes": 0, "by_subject": {"math": 0, "english": 0}, "by_error_type": {}, "resolved_count": 0, "by_source": {}},
        "review_schedule": []
    })

    today = datetime.now().strftime("%Y-%m-%d")
    added = 0
    duplicates = 0
    added_records = []

    for m in mistakes_list:
        subject = m.get("subject", "")
        kp = m.get("knowledge_point", "")
        qc = m.get("question_content", "")

        # 检查是否已有非常相似的活跃错题（模糊匹配避免重复录入）
        is_duplicate = False
        for existing in existing_book["mistakes"]:
            if (existing.get("status") != "mastered" and
                _similarity_check(qc, existing.get("question_content", "")) and
                existing.get("subject") == subject):
                # 已存在相似条目 → 更新错误次数
                existing["error_count"] = existing.get("error_count", 1) + 1
                existing["last_review_date"] = today
                next_days = _get_next_interval(existing["error_count"])
                existing["next_review_date"] = (
                    datetime.now() + timedelta(days=next_days)
                ).strftime("%Y-%m-%d")
                is_duplicate = True
                duplicates += 1
                logger.info(f"  🔄 匹配到相似条目，更新错误次数→{existing['error_count']}")
                break

        if is_duplicate:
            continue

        # 新建错题记录
        source = m.get("source", "手动录入(飞书)")
        new_record = {
            "date": today,
            "subject": subject,
            "question_id": f"MANUAL-{len(existing_book['mistakes']) + added + 1}",
            "question_type": m.get("question_type", ""),
            "question_content": qc,
            "student_answer": m.get("student_answer", ""),
            "correct_answer": m.get("correct_answer", ""),
            "error_reason": m.get("error_reason", ""),
            "knowledge_point": kp,
            "error_count": 1,
            "status": "new",
            "source": source,
            "last_review_date": today,
            "next_review_date": (
                datetime.now() + timedelta(days=1)
            ).strftime("%Y-%m-%d"),
            "variant_questions": []
        }
        existing_book["mistakes"].append(new_record)
        added_records.append(new_record)
        added += 1
        logger.info(f"  ✅ 新增手动错题: {subject}/{kp} (来源:{source})")

    # 更新统计数据
    existing_book["stats"]["total_mistakes"] = len(existing_book["mistakes"])
    for subj in ["math", "english"]:
        existing_book["stats"]["by_subject"][subj] = sum(
            1 for m in existing_book["mistakes"] if m.get("subject") == subj
        )

    # 按来源统计
    by_source = {}
    for m in existing_book["mistakes"]:
        s = m.get("source", "未知")
        by_source[s] = by_source.get(s, 0) + 1
    existing_book["stats"]["by_source"] = by_source

    # 写回文件
    write_json_safe(MISTAKE_BOOK_FILE, existing_book)
    logger.info(f"💾 错题本已更新(手动录入): +{added} 条, 重复{duplicates}条, 总计 {len(existing_book['mistakes'])} 条")

    return {
        "added": added,
        "duplicates": duplicates,
        "added_records": added_records,
        "total_count": len(existing_book["mistakes"])
    }


def _similarity_check(content1: str, content2: str, threshold: float = 0.6) -> bool:
    """
    简单的文本相似度检查
    如果两个题目内容的关键词重叠度超过阈值则认为相似
    （避免同一道题被重复录入）
    """
    if not content1 or not content2:
        return False

    # 简单方案：取较短的文本，看它的关键词是否大部分出现在较长文本中
    shorter, longer = (content1, content2) if len(content1) < len(content2) else (content2, content1)

    # 提取短文中的数字和中文关键词（去除标点和空白）
    import string as _string
    def extract_keywords(text):
        # 保留数字、英文字母和中文字符
        chars = []
        for ch in text:
            if ch.isdigit() or ch.isalpha() or '\u4e00' <= ch <= '\u9fff':
                chars.append(ch.lower())
        return set(chars)

    kw_short = extract_keywords(shorter)
    kw_long = extract_keywords(longer)

    if not kw_short:
        return False

    overlap = kw_short & kw_long
    ratio = len(overlap) / len(kw_short)

    return ratio >= threshold


def _get_next_interval(error_count: int) -> int:
    """根据错误次数计算艾宾浩斯间隔天数: 1d → 3d → 7d → 14d → 14d(循环)"""
    intervals = [1, 3, 7, 14]
    idx = min(error_count - 1, len(intervals) - 1)
    return intervals[idx]


# ==================== 飞书消息发送 ====================

def send_feishu_message(receive_id: str, text: str, message_id: str = None, receive_id_type: str = "open_id") -> bool:
    """
    发送消息到飞书聊天窗口
    receive_id: 接收者ID（通常是 open_id）
    receive_id_type: ID类型，默认 open_id（支持 open_id / chat_id / user_id）
    支持文本和卡片格式
    """
    import requests

    # 获取 tenant_access_token
    token_url = "https://open.feishu.cn/open-apis/auth/v3/tenant_access_token/internal"
    token_resp = requests.post(token_url, json={
        "app_id": FEISHU_APP_ID,
        "app_secret": FEISHU_APP_SECRET
    })
    token_data = token_resp.json()

    if token_data.get("code") != 0:
        logger.error(f"获取token失败: {token_data}")
        return False

    token = token_data["tenant_access_token"]

    # 发送消息（使用动态 receive_id_type）
    send_url = f"https://open.feishu.cn/open-apis/im/v1/messages?receive_id_type={receive_id_type}"

    # 如果内容较长，使用卡片格式；否则用纯文本
    if len(text) > 500:
        # 使用富文本来保留格式
        msg_content = json.dumps({"text": text}, ensure_ascii=False)
        msg_type = "text"
    else:
        msg_content = json.dumps({"text": text}, ensure_ascii=False)
        msg_type = "text"

    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json; charset=utf-8"
    }

    body = {
        "receive_id": receive_id,
        "msg_type": msg_type,
        "content": msg_content
    }

    resp = requests.post(send_url, headers=headers, json=body)
    result = resp.json()

    if result.get("code") == 0:
        logger.info(f"消息发送成功! message_id={result.get('data', {}).get('message_id')}")
        return True
    else:
        logger.error(f"消息发送失败: {result}")
        return False


# ==================== 飞书长连接事件处理 ====================

def handle_im_message(event):
    """
    接收飞书消息的核心处理函数
    参数: P2ImMessageReceiveV1 事件对象（包含 event.sender 和 event.message）
    """
    try:
        # 提取消息内容
        message = event.event.message
        message_id = message.message_id
        sender = event.event.sender
        
        # 获取发送者标识（优先 open_id，用于回复消息）
        receive_id = None
        receive_id_type = "open_id"  # 飞书 API 用 open_id 发私信最可靠
        if sender and sender.sender_id:
            # 尝试获取 open_id
            receive_id = getattr(sender.sender_id, 'open_id', None)
            if not receive_id:
                receive_id = getattr(sender.sender_id, 'user_id', None)
                receive_id_type = "user_id" if receive_id else "open_id"
            # 同时记录 chat_id 用于日志
            chat_id = getattr(sender.sender_id, 'chat_id', None)
        else:
            chat_id = None

        # 解析消息内容
        content_json = json.loads(message.content) if message.content else {}
        user_text = content_json.get("text", "")

        if not user_text.strip():
            logger.warning(f"收到空消息: {message_id}")
            return

        logger.info(f"📨 收到消息 | open_id={receive_id} | chat_id={chat_id} | msg_id={message_id} | 内容: {user_text[:100]}")

        # 处理消息（核心逻辑：判断指令 → 加载题目 → DeepSeek批改 → 回复）
        reply_text = process_message(user_text)

        # 发送回复到飞书
        if receive_id:
            success = send_feishu_message(receive_id, reply_text, str(message_id), receive_id_type)
            if not success:
                logger.error("❌ 回复消息失败!")
        else:
            logger.error(f"❌ 无法获取发送者ID! sender: {sender}")

    except Exception as e:
        logger.error(f"处理消息异常: {e}", exc_info=True)


# ==================== 主程序入口 ====================

def main():
    """启动机器人服务（长连接模式）"""
    logger.info("=" * 60)
    logger.info("🐱 小肥猫学习 - 飞书机器人后端服务 启动中...")
    logger.info("=" * 60)

    # 检查关键文件
    logger.info(f"工作目录: {WORK_DIR}")
    logger.info(f"System Prompt: {SYSTEM_PROMPT_FILE.exists()}")
    logger.info(f"Daily Questions: {DAILY_QUESTIONS_FILE.exists()}")
    logger.info(f"Mistake Book: {MISTAKE_BOOK_FILE.exists()}")

    if not DEEPSEEK_API_KEY:
        logger.warning("⚠️ DEEPSEEK_API_KEY 未设置！")

    # 创建事件处理器（直接注册函数到 builder）
    dispatcher = lark_oapi.EventDispatcherHandler.builder("", "") \
        .register_p2_im_message_receive_v1(handle_im_message) \
        .build()
    
    # 创建 WebSocket 长连接客户端（直接传入 app_id, app_secret, handler）
    ws_client = lark_oapi.ws.Client(
        app_id=FEISHU_APP_ID,
        app_secret=FEISHU_APP_SECRET,
        event_handler=dispatcher,
        log_level=lark_oapi.core.enum.LogLevel.INFO
    )

    logger.info("\n✅ 连接飞书服务器中... (长连接模式)")
    logger.info(f"   App ID: {FEISHU_APP_ID}")
    logger.info("   按 Ctrl+C 停止服务\n")

    # 启动长连接（阻塞主线程）
    try:
        ws_client.start()
    except KeyboardInterrupt:
        logger.info("\n👋 服务已停止")
    except Exception as e:
        logger.error(f"长连接错误: {e}", exc_info=True)
        logger.error("\n可能的原因:")
        logger.error("  1. 飞书开放平台的事件订阅未开启或未选择'长连接'模式")
        logger.error("  2. 应用未发布版本")
        logger.error("  3. 权限不足")
        logger.error("请检查 https://open.feishu.cn/app/cli_aa8f8d25a925dbea 的配置")


if __name__ == "__main__":
    main()
