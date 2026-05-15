#!/usr/bin/env python3
"""
小肥猫学习 - Vercel Serverless 批改端点
========================================
飞书事件回调 → 从 Bitable 读取题目 → DeepSeek 批改 → 错题入库 → 卡片回复
域名: https://xiaofeimao-cloud-mindywang19871129s-projects.vercel.app/
"""

import os
import json
import re
import time
import logging
from datetime import datetime
from typing import Optional
from http.server import BaseHTTPRequestHandler
from urllib.parse import urlparse

import httpx
from openai import OpenAI

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
                    datefmt="%Y-%m-%d %H:%M:%S")
logger = logging.getLogger("xiaofeimao")

# === 环境变量 ===
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
USER_OPEN_ID = os.environ.get("USER_OPEN_ID", "ou_8bf3770ed43ce0f273c7a34f1597cfe9")

_fs_token_cache = {"token": "", "expires_at": 0}

def get_feishu_token():
    now = time.time()
    if _fs_token_cache["token"] and _fs_token_cache["expires_at"] > now + 60:
        return _fs_token_cache["token"]
    resp = httpx.post(f"{FEISHU_API_BASE}/auth/v3/tenant_access_token/internal",
                      json={"app_id": FEISHU_APP_ID, "app_secret": FEISHU_APP_SECRET}, timeout=10)
    data = resp.json()
    if data.get("code") != 0:
        raise Exception(f"飞书Token失败: {data}")
    _fs_token_cache["token"] = data["tenant_access_token"]
    _fs_token_cache["expires_at"] = now + data.get("expire", 1800)
    return _fs_token_cache["token"]

def _feishu_get(path, params=None):
    token = get_feishu_token()
    headers = {"Authorization": f"Bearer {token}"}
    resp = httpx.get(f"{FEISHU_API_BASE}{path}", headers=headers, params=params, timeout=15)
    return resp.json()

def _feishu_post(path, body):
    token = get_feishu_token()
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json; charset=utf-8"}
    resp = httpx.post(f"{FEISHU_API_BASE}{path}", headers=headers, json=body, timeout=15)
    return resp.json()

def bitable_list_records(table_id, filter_str=None):
    url = f"/bitable/v1/apps/{BITABLE_APP_TOKEN}/tables/{table_id}/records"
    all_records = []
    page_token = None
    while True:
        params = {"page_size": 200}
        if filter_str:
            params["filter"] = filter_str
        if page_token:
            params["page_token"] = page_token
        data = _feishu_get(url, params)
        if data.get("code") != 0:
            logger.error(f"读取Bitable失败: {data}")
            break
        items = data.get("data", {}).get("items", [])
        all_records.extend(items)
        if not data.get("data", {}).get("has_more", False):
            break
        page_token = data["data"]["page_token"]
    return all_records

def bitable_add_record(table_id, fields):
    url = f"/bitable/v1/apps/{BITABLE_APP_TOKEN}/tables/{table_id}/records"
    data = _feishu_post(url, {"fields": fields})
    if data.get("code") != 0:
        logger.error(f"添加记录失败: {data}")
        return None
    return data["data"]["record"]["record_id"]

def send_card(open_id, title, content):
    card = {"config": {"wide_screen_mode": True},
            "header": {"title": {"tag": "plain_text", "content": title}, "template": "blue"},
            "elements": [{"tag": "markdown", "content": content}]}
    body = {"receive_id": open_id, "msg_type": "interactive",
            "content": json.dumps(card, ensure_ascii=False)}
    return _feishu_post(f"/im/v1/messages?receive_id_type=open_id", body).get("code") == 0

def send_text(open_id, text):
    body = {"receive_id": open_id, "msg_type": "text",
            "content": json.dumps({"text": text}, ensure_ascii=False)}
    return _feishu_post(f"/im/v1/messages?receive_id_type=open_id", body).get("code") == 0

# === 答案解析 ===
def parse_answers(text, questions):
    text = text.strip()
    parsed = {}
    if "|" in text and re.search(r'\|\d+\|', text):
        parts = text.split("|")
        i = 0
        while i < len(parts) - 1:
            p = parts[i].strip()
            if p.isdigit():
                parsed[int(p)] = parts[i+1].strip() if i+1 < len(parts) else ""
                i += 2
            else:
                i += 1
        return parsed
    if "=" in text:
        for m in re.finditer(r'([MEmM][\d]+)\s*=\s*([^\s,，]+(?:[\s,，/]+[^\s=,，]+)*)', text):
            parsed[m.group(1).upper()] = m.group(2).strip()
        return parsed
    tokens = [t.strip() for t in re.split(r'[\s,，]+', text) if t.strip()]
    for i, q in enumerate(questions):
        if i < len(tokens):
            parsed[q.get("id", f"Q{i+1}")] = tokens[i]
    return parsed

def normalize(ans):
    return ans.strip().lower().replace(" ", "")

# === DeepSeek 批改 ===
def grade_with_ai(q, student_answer):
    correct = str(q.get("correct_answer", ""))
    q_type = q.get("type", "")
    if normalize(student_answer) == normalize(correct):
        return {"correct": True, "analysis": "答案完全正确", "error_reason": ""}
    if q_type in ["口算速算", "竖式计算", "计算", "脱式计算", "填空"]:
        s_nums = re.findall(r'-?\d+\.?\d*', student_answer)
        c_nums = re.findall(r'-?\d+\.?\d*', correct)
        if s_nums == c_nums:
            return {"correct": True, "analysis": "数值正确", "error_reason": ""}
    return ai_deep(q, student_answer, correct)

def ai_deep(q, student_answer, correct):
    if not DEEPSEEK_API_KEY:
        return {"correct": False, "analysis": f"正确答案: {correct}",
                "error_reason": "DeepSeek API未配置"}
    try:
        client = OpenAI(api_key=DEEPSEEK_API_KEY, base_url=DEEPSEEK_BASE_URL)
        resp = client.chat.completions.create(model=DEEPSEEK_MODEL, temperature=0.1, max_tokens=500, messages=[
            {"role": "system", "content": "三年级批改助手。只输出JSON。"},
            {"role": "user", "content": f"""批改本题：
题目: {q.get('content','')}
类型: {q.get('type','')}
正确答案: {correct}
学生答案: {student_answer}
知识点: {q.get('knowledge_point','')}
JSON格式: {{"correct": true/false, "analysis": "...", "error_reason": "..."}}"""}
        ])
        text = resp.choices[0].message.content.strip()
        if text.startswith("```"):
            text = re.sub(r'^```(?:json)?\s*', '', text)
            text = re.sub(r'```\s*$', '', text)
        r = json.loads(text)
        return {"correct": r.get("correct", False), "analysis": r.get("analysis", ""),
                "error_reason": r.get("error_reason", "")}
    except Exception as e:
        logger.error(f"DeepSeek异常: {e}")
        return {"correct": False, "analysis": f"正确答案: {correct}", "error_reason": str(e)[:50]}

# === 错题入库 ===
def save_mistake(q, student_answer, grade_result, date):
    if grade_result["correct"]:
        return True
    now = datetime.now().strftime("%Y-%m-%d %H:%M")
    fields = {
        "日期": date, "科目": "数学" if q.get("id","").startswith("M") else "英语",
        "题号": q.get("id",""), "题型": q.get("type",""),
        "题目内容": q.get("content","")[:2000], "孩子答案": str(student_answer)[:1000],
        "正确答案": str(q.get("correct_answer",""))[:1000],
        "错因分析": grade_result.get("error_reason","")[:2000],
        "知识点": q.get("knowledge_point",""), "错误次数": 1,
        "状态": "新错题", "来源": "Vercel云批改", "是否已同步": False,
    }
    try:
        dt = datetime.strptime(now, "%Y-%m-%d %H:%M")
        fields["录入时间"] = int(dt.timestamp() * 1000)
    except:
        fields["录入时间"] = int(datetime.now().timestamp() * 1000)
    rid = bitable_add_record(BITABLE_MISTAKE_TABLE_ID, fields)
    if rid:
        logger.info(f"错题入库: {q.get('id')}")
    return bool(rid)

# === 主批改 ===
def grade_submission(text, date):
    today = date or datetime.now().strftime("%Y-%m-%d")
    records = bitable_list_records(BITABLE_DAILY_TABLE_ID, f'CurrentValue.[日期] = "{today}"')
    if not records:
        return {"success": False, "summary": f"📭 {today} 还没有题目记录", "details": []}
    questions = []
    for rec in records:
        f = rec.get("fields", {})
        questions.append({"id": f.get("题号",""), "num": int(f.get("题号","0").lstrip("MEme") or 0),
                          "type": f.get("题型",""), "content": f.get("题目内容",""),
                          "correct_answer": f.get("正确答案",""), "knowledge_point": f.get("知识点",""),
                          "score": f.get("分值",0), "subject": f.get("科目","")})
    questions.sort(key=lambda x: (0 if x["subject"]=="数学" else 1, x["num"]))
    answers = parse_answers(text, questions)
    logger.info(f"解析答案: {answers}")
    results, total, earned, correct_n, wrong_n = [], 0, 0, 0, 0
    for q in questions:
        qid = q["id"]
        sa = answers.get(qid, answers.get(q["num"], ""))
        if not sa:
            results.append({"question": q, "student_answer": "（未作答）", "correct": False,
                            "analysis": "未提交答案", "error_reason": "未作答"})
            wrong_n += 1; total += q.get("score",0); continue
        g = grade_with_ai(q, sa)
        total += q.get("score",0)
        if g["correct"]:
            earned += q.get("score",0); correct_n += 1
        else:
            wrong_n += 1; save_mistake(q, sa, g, today)
        results.append({"question": q, "student_answer": sa, **g})
    rate = round(earned/total*100,1) if total > 0 else 0
    emoji = "🎉" if rate>=90 else "👍" if rate>=70 else "💪" if rate>=50 else "📚"
    comment = {90:"太棒了", 70:"不错", 50:"加油"}.get((rate//10)*10, "继续努力")
    summary = f"━━━━━━━━━━━━━━━\n📊 今日成绩：{earned}/{total}（{rate}%）\n✅ 正确 {correct_n} | ❌ 错误 {wrong_n}\n{emoji} {comment}"
    return {"success": True, "total_score": total, "earned_score": earned,
            "correct_count": correct_n, "wrong_count": wrong_n, "pass_rate": rate,
            "summary": summary, "details": results}

def format_card(result):
    title = f"📝 批改结果 · {datetime.now().strftime('%m月%d日')}"
    if not result.get("success"):
        return title, result.get("summary", "批改遇到问题")
    content = result["summary"] + "\n\n"
    for i, item in enumerate(result["details"], 1):
        q = item["question"]
        mark = "✅" if item["correct"] else "❌"
        si = "📐" if q.get("subject")=="数学" else "📘"
        content += f"**{si} 第{i}题【{q.get('type','')}】({q.get('score',0)}分) {mark}**\n"
        content += f"✏️ 孩子答案：{item.get('student_answer','')}\n"
        if not item["correct"]:
            content += f"✅ 正确答案：{q.get('correct_answer','')}\n"
            content += f"💡 解析：{item.get('analysis','')}\n"
            if item.get("error_reason"):
                content += f"⚠️ 错因：{item.get('error_reason','')}\n📒 已录入错题本 ✓\n"
        content += "\n"
    content += "---\n> 🐱 小肥猫学习·Vercel云批改\n> 💡 错题已自动记录"
    return title, content

def _is_command(text):
    cmds = ["增加需求如下","新需求","查看错题本","错题查询","生成今日练习","出今天的题",
            "暂停推送","停止推送","恢复推送","开始推送","录错题","加入错题本","记录错题","手动录错"]
    return any(c in text.lower().strip() for c in cmds)

# === Vercel Handler ===
class handler(BaseHTTPRequestHandler):
    def do_GET(self):
        self._json(200, json.dumps({"status": "ok", "service": "xiaofeimao", "version": "2.0.0"}))

    def do_POST(self):
        try:
            cl = int(self.headers.get("Content-Length", 0))
            if cl == 0:
                return self._json(400, '{"error":"empty body"}')
            body = json.loads(self.rfile.read(cl).decode("utf-8"))
            et = body.get("type", "")
            logger.info(f"事件: {et}")

            if et == "url_verification":
                token = body.get("token","")
                challenge = body.get("challenge","")
                if FEISHU_VERIFICATION_TOKEN and token != FEISHU_VERIFICATION_TOKEN:
                    return self._json(403, '{"error":"bad token"}')
                return self._json(200, json.dumps({"challenge": challenge}))

            if et == "im.message.receive_v1" or "message" in str(body):
                event = body.get("event", body)
                msg = event.get("message", {})
                if msg.get("message_type") != "text":
                    logger.info(f"跳过: {msg.get('message_type')}")
                    return self._json(200, '{"code":0}')
                mc = json.loads(msg.get("content","{}"))
                text = mc.get("text","").strip()
                if not text:
                    return self._json(200, '{"code":0}')
                sid = event.get("sender",{}).get("sender_id",{}).get("open_id", USER_OPEN_ID)
                ts = msg.get("create_time","")
                mdate = datetime.fromtimestamp(int(ts)/1000).strftime("%Y-%m-%d") if ts else datetime.now().strftime("%Y-%m-%d")
                logger.info(f"消息: {sid} {text[:80]}")

                if _is_command(text):
                    logger.info(f"跳过指令: {text[:50]}")
                    return self._json(200, '{"code":0}')

                logger.info("开始批改...")
                result = grade_submission(text, mdate)
                if result["success"]:
                    title, content = format_card(result)
                    send_card(sid, title, content)
                    logger.info(f"批改完成: {result['correct_count']}✓/{result['wrong_count']}✗ {result['pass_rate']}%")
                else:
                    send_text(sid, result.get("summary","批改未成功"))
                return self._json(200, '{"code":0}')

            return self._json(200, '{"code":0}')
        except Exception as e:
            logger.error(f"POST异常: {e}", exc_info=True)
            return self._json(200, json.dumps({"code":-1,"msg":str(e)[:200]}))

    def _json(self, status, body):
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.end_headers()
        self.wfile.write(body.encode("utf-8"))

    def log_message(self, *args):
        pass
