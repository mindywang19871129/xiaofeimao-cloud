#!/usr/bin/env python3
"""
小肥猫 v2.2 端到端测试
========================
测试流程：
  1. 飞书 API 连通性（获取 token）
  2. 生成今日题目
  3. 模拟批改流程
  4. 发送测试结果到飞书
"""

import sys, os, json, time, requests

# ===== 配置 =====
APP_ID = "cli_aa8f8d25a925dbea"
APP_SECRET = "9vyD11qA4jIxn3PCQB1jnfvTXMXs2Rve"
USER_OPEN_ID = "ou_8bf3770ed43ce0f273c7a34f1597cfe9"
DEEPSEEK_API_KEY = os.environ.get("DEEPSEEK_API_KEY", "sk-f5d41971d21d46ffbdd4e1d7af4a093c")

BASE_URL = "https://open.feishu.cn/open-apis"

PASS = 0
FAIL = 0
results = []

def ok(msg):
    global PASS
    PASS += 1
    results.append(f"✅ {msg}")
    print(f"  ✅ {msg}")

def ng(msg):
    global FAIL
    FAIL += 1
    results.append(f"❌ {msg}")
    print(f"  ❌ {msg}")

def hr(title):
    print(f"\n{'='*50}")
    print(f"  {title}")
    print(f"{'='*50}")

# ===== 测试 1：飞书 API 连通性 =====
hr("📡 测试 1：飞书 API 连通性")

try:
    resp = requests.post(f"{BASE_URL}/auth/v3/tenant_access_token/internal", json={
        "app_id": APP_ID,
        "app_secret": APP_SECRET
    }, timeout=15)
    data = resp.json()
    code = data.get("code", -1)
    if code == 0:
        token = data["tenant_access_token"]
        ok(f"Token 获取成功 ({token[:20]}...)")
    else:
        ng(f"Token 获取失败: code={code}, msg={data.get('msg')}")
        sys.exit(1)
except Exception as e:
    ng(f"网络异常: {e}")
    sys.exit(1)

# ===== 测试 2：生成今日题目 =====
hr("📝 测试 2：生成今日题目")

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "cloud_function", "ws-server"))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.environ["DEEPSEEK_API_KEY"] = DEEPSEEK_API_KEY

try:
    from question_generator import MATH_TOPICS
    ok(f"教材数据加载成功: {len(MATH_TOPICS)} 天循环")
except Exception as e:
    ng(f"教材加载失败: {e}")

# 检查今日应该出什么题
from datetime import date, datetime
today = date.today()
start = date(2026, 5, 14)
day_num = (today - start).days % 15
if day_num < len(MATH_TOPICS):
    topic = MATH_TOPICS[day_num]
    ok(f"今日第 {day_num+1} 天: {topic['unit']} - {topic['name']}")
    ok(f"知识点数: {len(topic['knowledge_points'])}")
else:
    ok(f"今日第 {day_num+1} 天（综合复习）")

# ===== 测试 3：模拟批改 =====
hr("🔍 测试 3：模拟批改引擎")

# 构造模拟题目和答案
mock_questions = [
    {"id": "M1", "type": "计算", "content": "23 × 45 = ?", "answer": "1035", "score": 5},
    {"id": "M2", "type": "填空", "content": "长方形的周长 = (长 + 宽) × ?", "answer": "2", "score": 3},
    {"id": "M3", "type": "选择", "content": "平移后图形的大小会变吗？", "answer": "不变", "score": 2},
]

from grading import parse_answers_with_ai

test_answer = "M1=1035 M2=2 M3=不变"
try:
    parsed = parse_answers_with_ai(test_answer, mock_questions)
    ok(f"AI 答案解析成功: {len(parsed)} 题")
    for qid, ans in parsed.items():
        print(f"     {qid}: 作答={ans}")
except Exception as e:
    ng(f"AI 解析异常: {str(e)[:80]}")

# 模拟评分逻辑
correct_count = 0
for q in mock_questions:
    qid = q["id"]
    student_ans = parsed.get(qid, "")
    expected = q["answer"]
    if student_ans.strip() == expected.strip():
        correct_count += 1
        print(f"     {qid}: ✅ 正确 ({student_ans})")
    else:
        print(f"     {qid}: ❌ 期望={expected}, 作答={student_ans}")

score_rate = correct_count / len(mock_questions) * 100
ok(f"评分完成: {correct_count}/{len(mock_questions)} 正确, 得分率 {score_rate:.0f}%")

# ===== 测试 4：发送测试结果到飞书 =====
hr("📨 测试 4：发送结果到飞书")

try:
    msg_content = {
        "config": {"wide_screen_mode": True},
        "header": {
            "title": {"tag": "plain_text", "content": "🐱 小肥猫 v2.2 E2E 测试报告"},
            "template": "blue"
        },
        "elements": [
            {"tag": "div", "text": {"tag": "lark_md", "content": f"**测试时间**: {datetime.now().strftime('%Y-%m-%d %H:%M')}"}},
            {"tag": "div", "text": {"tag": "lark_md", "content": f"**今日课程**: 第{day_num+1}天 {topic['unit']} - {topic['name']}"}},
            {"tag": "hr"},
            {"tag": "div", "text": {"tag": "lark_md", "content": "\n".join(results)}},
            {"tag": "hr"},
            {"tag": "div", "text": {"tag": "lark_md", "content": f"**总计**: {PASS} 通过 / {FAIL} 失败 / {PASS+FAIL} 项"}},
            {"tag": "div", "text": {"tag": "lark_md", "content": "🟢 服务运行正常" if FAIL == 0 else "🔴 存在问题需修复"}},
        ]
    }
    
    resp = requests.post(
        f"{BASE_URL}/im/v1/messages?receive_id_type=open_id",
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
        json={"receive_id": USER_OPEN_ID, "msg_type": "interactive", "content": json.dumps(msg_content)},
        timeout=15
    )
    data = resp.json()
    if data.get("code") == 0:
        ok(f"测试报告已发送到飞书 (msg_id: {data['data']['message_id']})")
    else:
        ng(f"发送失败: code={data.get('code')}, msg={data.get('msg')}")
except Exception as e:
    ng(f"发送异常: {str(e)[:80]}")

# ===== 汇总 =====
hr("📊 测试汇总")
print(f"  通过: {PASS}  |  失败: {FAIL}  |  总计: {PASS+FAIL}")
if FAIL == 0:
    print(f"\n  🎉 全部通过！小肥猫 v2.2 运行正常")
else:
    print(f"\n  ⚠️  有 {FAIL} 项失败，请检查")
