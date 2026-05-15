#!/usr/bin/env python3
"""
小肥猫学习 - EdgeOne Pages 云函数（批改端点）
=============================================
功能：
1. 接收飞书事件回调（URL 验证 + 消息事件）
2. 从飞书多维表格读取当日题目
3. 调用 DeepSeek API 批改答案
4. 错题自动写入多维表格错题本
5. 通过飞书 API 回复批改结果

部署方式：
- EdgeOne Pages: git push 自动部署
- 飞书事件订阅 URL: https://<project>.edgeone.site/

文件结构（EdgeOne Pages）：
  cloud-functions/
  ├── index.py           ← 本文件（根路由，GET/POST /）
  └── requirements.txt

环境变量（在 EdgeOne Pages 后台设置）：
- DEEPSEEK_API_KEY
- FEISHU_APP_ID / FEISHU_APP_SECRET
- FEISHU_VERIFICATION_TOKEN（飞书事件订阅的 Verification Token）
- BITABLE_APP_TOKEN / BITABLE_DAILY_TABLE_ID / BITABLE_MISTAKE_TABLE_ID
- USER_OPEN_ID
"""

import os
import json
import re
import time
import hashlib
import logging
from datetime import datetime
from typing import Optional

import httpx
from openai import OpenAI
from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import JSONResponse

# ==================== FastAPI App ====================

app = FastAPI(title="小肥猫学习·云批改", version="1.0.0")

# ==================== 配置（从环境变量读取） ====================

DEEPSEEK_API_KEY = os.environ.get("DEEPSEEK_API_KEY", "")
DEEPSEEK_BASE_URL = "https://api.deepseek.com"
DEEPSEEK_MODEL = "deepseek-chat"

FEISHU_APP_ID = os.environ.get("FEISHU_APP_ID", "")
FEISHU_APP_SECRET = os.environ.get("FEISHU_APP_SECRET", "")
FEISHU_VERIFICATION_TOKEN = os.environ.get("FEISHU_VERIFICATION_TOKEN", "")
FEISHU_API_BASE = "https://open.feishu.cn/open-apis"

BITABLE_APP_TOKEN = os.environ.get("BITABLE_APP_TOKEN", "")
BITABLE_DAILY_TABLE_ID = os.environ.get("BITABLE_DAILY_TABLE_ID", "")
BITABLE_MISTAKE_TABLE_ID = os.environ.get("BITABLE_MISTAKE_TABLE_ID", "")

# 用户 open_id（用于私聊回复）
USER_OPEN_ID = os.environ.get("USER_OPEN_ID", "ou_8bf3770ed43ce0f273c7a34f1597cfe9")

# 日志
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("cloud_grading")


# ==================== 飞书 Token ====================

_fs_token_cache = {"token": "", "expires_at": 0}


def get_feishu_token() -> str:
    """获取飞书 tenant_access_token（带内存缓存）"""
    now = time.time()
    if _fs_token_cache["token"] and _fs_token_cache["expires_at"] > now + 60:
        return _fs_token_cache["token"]

    url = f"{FEISHU_API_BASE}/auth/v3/tenant_access_token/internal"
    resp = httpx.post(url, json={
        "app_id": FEISHU_APP_ID,
        "app_secret": FEISHU_APP_SECRET
    }, timeout=10)
    data = resp.json()

    if data.get("code") != 0:
        raise Exception(f"获取飞书Token失败: {data}")

    token = data["tenant_access_token"]
    expires_in = data.get("expire", 1800)
    _fs_token_cache["token"] = token
    _fs_token_cache["expires_at"] = now + expires_in
    return token


# ==================== 飞书 Bitable 操作 ====================

def bitable_list_records(table_id: str, filter_str: str = None) -> list:
    """从 Bitable 读取记录（自动翻页）"""
    token = get_feishu_token()
    url = f"{FEISHU_API_BASE}/bitable/v1/apps/{BITABLE_APP_TOKEN}/tables/{table_id}/records"
    headers = {"Authorization": f"Bearer {token}"}

    all_records = []
    page_token = None

    while True:
        params = {"page_size": 200}
        if filter_str:
            params["filter"] = filter_str
        if page_token:
            params["page_token"] = page_token

        resp = httpx.get(url, headers=headers, params=params, timeout=15)
        data = resp.json()

        if data.get("code") != 0:
            logger.error(f"读取Bitable失败: {data}")
            break

        items = data.get("data", {}).get("items", [])
        all_records.extend(items)

        if not data.get("data", {}).get("has_more", False):
            break
        page_token = data["data"]["page_token"]

    return all_records


def bitable_add_record(table_id: str, fields: dict) -> Optional[str]:
    """添加单条记录到 Bitable"""
    token = get_feishu_token()
    url = f"{FEISHU_API_BASE}/bitable/v1/apps/{BITABLE_APP_TOKEN}/tables/{table_id}/records"
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json; charset=utf-8"
    }

    body = {"fields": fields}
    resp = httpx.post(url, headers=headers, json=body, timeout=15)
    data = resp.json()

    if data.get("code") != 0:
        logger.error(f"添加Bitable记录失败: {data}")
        return None
    return data["data"]["record"]["record_id"]


# ==================== 飞书消息发送 ====================

def send_feishu_card(open_id: str, title: str, content: str) -> bool:
    """发送飞书卡片消息"""
    token = get_feishu_token()
    url = f"{FEISHU_API_BASE}/im/v1/messages?receive_id_type=open_id"
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json; charset=utf-8"
    }

    card = {
        "config": {"wide_screen_mode": True},
        "header": {
            "title": {"tag": "plain_text", "content": title},
            "template": "blue"
        },
        "elements": [
            {"tag": "markdown", "content": content}
        ]
    }

    body = {
        "receive_id": open_id,
        "msg_type": "interactive",
        "content": json.dumps(card)
    }

    resp = httpx.post(url, headers=headers, json=body, timeout=15)
    data = resp.json()
    return data.get("code") == 0


def send_feishu_text(open_id: str, text: str) -> bool:
    """发送飞书文本消息（备用）"""
    token = get_feishu_token()
    url = f"{FEISHU_API_BASE}/im/v1/messages?receive_id_type=open_id"
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json; charset=utf-8"
    }

    body = {
        "receive_id": open_id,
        "msg_type": "text",
        "content": json.dumps({"text": text})
    }

    resp = httpx.post(url, headers=headers, json=body, timeout=15)
    return resp.json().get("code") == 0


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
    # 按空白/逗号分隔
    tokens = re.split(r'[\s,，]+', message_text)
    tokens = [t.strip() for t in tokens if t.strip()]

    for i, q in enumerate(questions):
        if i < len(tokens):
            # 尝试匹配题号
            q_id = q.get("id", f"Q{i+1}")
            parsed[q_id] = tokens[i]

    return parsed


def normalize_answer(ans: str) -> str:
    """标准化答案（忽略大小写、空格差异）"""
    return ans.strip().lower().replace(" ", "")


# ==================== DeepSeek 批改 ====================

def grade_with_ai(question: dict, student_answer: str) -> dict:
    """
    使用 DeepSeek 智能批改单道题
    Returns: {"correct": bool, "analysis": str, "error_reason": str}
    """
    # 先做精确匹配（快速路径）
    correct_answer = str(question.get("correct_answer", ""))
    q_type = question.get("type", "")

    # 精确匹配
    if normalize_answer(student_answer) == normalize_answer(correct_answer):
        return {"correct": True, "analysis": "答案完全正确", "error_reason": ""}

    # 模糊匹配（数学数字忽略单位差异）
    if q_type in ["口算速算", "竖式计算", "计算", "脱式计算", "填空"]:
        # 提取数字比较
        s_nums = re.findall(r'-?\d+\.?\d*', student_answer)
        c_nums = re.findall(r'-?\d+\.?\d*', correct_answer)
        if s_nums == c_nums:
            return {"correct": True, "analysis": "数值正确（可能有细微格式差异）", "error_reason": ""}

    # 如果以上都匹配不上 → 用 AI 深度批改
    return _ai_deep_grade(question, student_answer, correct_answer)


def _ai_deep_grade(question: dict, student_answer: str, correct_answer: str) -> dict:
    """DeepSeek 深度批改（准确判断 + 错因分析）"""
    if not DEEPSEEK_API_KEY:
        return {
            "correct": False,
            "analysis": f"正确答案应为: {correct_answer}",
            "error_reason": "DeepSeek API未配置，无法智能分析错因"
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
                {"role": "user", "content": prompt}
            ],
            temperature=0.1,
            max_tokens=500
        )

        text = resp.choices[0].message.content.strip()

        # 解析 JSON
        if text.startswith("```"):
            text = re.sub(r'^```(?:json)?\s*', '', text)
            text = re.sub(r'```\s*$', '', text)

        result = json.loads(text)
        return {
            "correct": result.get("correct", False),
            "analysis": result.get("analysis", "AI分析结果"),
            "error_reason": result.get("error_reason", "")
        }

    except Exception as e:
        logger.error(f"DeepSeek批改异常: {e}")
        # 降级：简单比对
        return {
            "correct": False,
            "analysis": f"正确答案: {correct_answer}",
            "error_reason": f"批改系统异常: {str(e)[:50]}"
        }


# ==================== 错题入库 ====================

def save_mistake_to_bitable(question: dict, student_answer: str, grade_result: dict,
                             message_date: str) -> bool:
    """将错题存入飞书多维表格错题本"""
    if grade_result["correct"]:
        return True  # 不错不存

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
        "来源": "云函数批改",
        "是否已同步": False,
        "录入时间": _date_to_timestamp(now_str),
    }

    rid = bitable_add_record(BITABLE_MISTAKE_TABLE_ID, fields)
    if rid:
        logger.info(f"✅ 错题已入库: {question.get('id')} → {question.get('knowledge_point')}")
        return True
    return False


def _date_to_timestamp(date_str: str, fmt: str = "%Y-%m-%d %H:%M") -> int:
    """日期字符串 → 毫秒时间戳"""
    try:
        dt = datetime.strptime(date_str, fmt)
        return int(dt.timestamp() * 1000)
    except ValueError:
        return int(datetime.now().timestamp() * 1000)


# ==================== 主批改流程 ====================

def grade_submission(message_text: str, message_date: str) -> dict:
    """
    完整批改流程
    1. 从 Bitable 读取当日题目
    2. 逐题批改
    3. 错题入库
    4. 返回批改结果
    """
    # Step 1: 读取当日题目
    today = message_date or datetime.now().strftime("%Y-%m-%d")
    filter_str = f'CurrentValue.[日期] = "{today}"'
    records = bitable_list_records(BITABLE_DAILY_TABLE_ID, filter_str)

    if not records:
        return {
            "success": False,
            "summary": f"📭 {today} 还没有题目记录。请确保 Mac 端已完成今日出题推送。",
            "details": []
        }

    # 转换为题目列表
    questions = []
    for rec in records:
        f = rec.get("fields", {})
        questions.append({
            "id": f.get("题号", ""),
            "num": int(f.get("题号", "0").lstrip("MEme") or 0),
            "type": f.get("题型", ""),
            "content": f.get("题目内容", ""),
            "correct_answer": f.get("正确答案", ""),
            "knowledge_point": f.get("知识点", ""),
            "score": f.get("分值", 0),
            "subject": f.get("科目", ""),
        })

    # 排序
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
                "error_reason": "未作答"
            })
            wrong_count += 1
            total_score += q.get("score", 0)
            continue

        # 批改
        grade = grade_with_ai(q, student_answer)
        total_score += q.get("score", 0)

        if grade["correct"]:
            earned_score += q.get("score", 0)
            correct_count += 1
        else:
            wrong_count += 1
            # 错题入库
            save_mistake_to_bitable(q, student_answer, grade, today)

        results.append({
            "question": q,
            "student_answer": student_answer,
            **grade
        })

    # Step 4: 构建批改结果
    all_wrong = wrong_count > 0 and correct_count == 0
    pass_rate = round(earned_score / total_score * 100, 1) if total_score > 0 else 0

    return {
        "success": True,
        "total_score": total_score,
        "earned_score": earned_score,
        "correct_count": correct_count,
        "wrong_count": wrong_count,
        "pass_rate": pass_rate,
        "summary": _build_summary(total_score, earned_score, correct_count, wrong_count, pass_rate),
        "details": results
    }


def _build_summary(total, earned, correct, wrong, rate):
    """构建总结语"""
    if rate >= 90:
        emoji = "🎉"
        comment = "太棒了！继续保持！"
    elif rate >= 70:
        emoji = "👍"
        comment = "不错，再细心一点会更好！"
    elif rate >= 50:
        emoji = "💪"
        comment = "还需要多多练习，加油！"
    else:
        emoji = "📚"
        comment = "别灰心，我们一起看看哪里可以改进。"

    return (
        f"━━━━━━━━━━━━━━━\n"
        f"📊 今日成绩：{earned}/{total} 分（得分率 {rate}%）\n"
        f"✅ 正确：{correct} 道 | ❌ 错误：{wrong} 道\n"
        f"{emoji} {comment}\n"
    )


def format_grading_card(result: dict) -> (str, str):
    """格式化批改结果为飞书卡片内容"""
    title = f"📝 批改结果 · {datetime.now().strftime('%m月%d日')}"

    if not result.get("success"):
        return title, result.get("summary", "批改遇到问题")

    content = result["summary"] + "\n\n"

    # 逐题详情
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
    content += "> 🐱 小肥猫学习·云端批改\n"
    content += "> 💡 错题已自动记录，下次练习会针对性复习！"

    return title, content


# ==================== API 路由 ====================

@app.get("/")
async def health_check():
    """健康检查 + 飞书 URL 验证"""
    return JSONResponse({"status": "ok", "service": "小肥猫学习·云批改", "version": "1.0.0"})


@app.post("/")
async def feishu_event(request: Request):
    """
    飞书事件订阅回调
    处理两类事件：
    1. URL 验证 (type=url_verification)
    2. 消息事件 (type=im.message.receive_v1)
    """
    try:
        body = await request.json()
        logger.info(f"收到飞书事件: type={body.get('type','?')}")

        # URL 验证
        if body.get("type") == "url_verification":
            token = body.get("token", "")
            challenge = body.get("challenge", "")
            # 验证 token
            if FEISHU_VERIFICATION_TOKEN and token != FEISHU_VERIFICATION_TOKEN:
                raise HTTPException(status_code=403, detail="Invalid verification token")
            return JSONResponse({"challenge": challenge})

        # 消息事件
        if body.get("type") == "im.message.receive_v1" or "message" in str(body):
            return await handle_message_event(body)

        # 其他事件直接ACK
        return JSONResponse({"code": 0})

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"事件处理异常: {e}", exc_info=True)
        return JSONResponse({"code": -1, "msg": str(e)[:200]}, status_code=200)


async def handle_message_event(body: dict):
    """处理飞书消息事件"""
    event = body.get("event", body)
    message = event.get("message", {})

    # 只处理文本消息
    if message.get("message_type") != "text":
        logger.info(f"忽略非文本消息: {message.get('message_type')}")
        return JSONResponse({"code": 0})

    # 提取消息内容
    msg_content = json.loads(message.get("content", "{}"))
    text = msg_content.get("text", "").strip()

    # 提取发送者 open_id
    sender_id = event.get("sender", {}).get("sender_id", {}).get("open_id", USER_OPEN_ID)

    # 获取消息时间
    msg_timestamp = message.get("create_time", "")
    msg_date = datetime.fromtimestamp(
        int(msg_timestamp) / 1000
    ).strftime("%Y-%m-%d") if msg_timestamp else datetime.now().strftime("%Y-%m-%d")

    logger.info(f"收到消息: sender={sender_id}, text={text[:100]}")

    # 判断是否为指令
    if _is_command(text):
        logger.info(f"识别为指令消息，跳过批改: {text[:50]}")
        return JSONResponse({"code": 0})

    # 执行批改
    logger.info("开始批改...")
    try:
        result = grade_submission(text, msg_date)

        if result["success"]:
            title, content = format_grading_card(result)
            send_feishu_card(sender_id, title, content)
            logger.info(f"批改完成: {result['correct_count']}✓/{result['wrong_count']}✗")
        else:
            send_feishu_text(sender_id, result.get("summary", "批改未成功，请稍后重试"))

    except Exception as e:
        logger.error(f"批改异常: {e}", exc_info=True)
        send_feishu_text(sender_id, f"⚠️ 批改遇到问题: {str(e)[:100]}\n请稍后重试或联系管理员。")

    return JSONResponse({"code": 0})


def _is_command(text: str) -> bool:
    """判断消息是否为指令（非答案提交）"""
    text_lower = text.lower().strip()
    commands = [
        "增加需求如下", "新需求", "查看错题本", "错题查询",
        "生成今日练习", "出今天的题", "暂停推送", "停止推送",
        "恢复推送", "开始推送", "录错题", "加入错题本",
        "记录错题", "手动录错",
    ]
    return any(cmd in text_lower for cmd in commands)


# ==================== 服务信息 ====================
# 路由已合并到 health_check (GET /) 和 feishu_event (POST /)
