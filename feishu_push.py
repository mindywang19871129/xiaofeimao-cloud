#!/usr/bin/env python3
"""
🐱 小肥猫学习 - 飞书每日学习卷推送
功能：
1. 获取飞书 tenant_access_token
2. 发送卡片消息到飞书机器人（私聊/群聊）
3. 可被 cron 定时任务调用（每天晚上8点自动推送）
"""
import requests
import json
import sys
import os
from datetime import datetime

# ================== 配置区 ==================
APP_ID = "cli_aa8f8d25a925dbea"
APP_SECRET = "9vyD11qA4jIxn3PCQB1jnfvTXMXs2Rve"

# 目标用户的 open_id（用户和机器人的私聊会话）
USER_OPEN_ID = "ou_8bf3770ed43ce0f273c7a34f1597cfe9"

# 接收者类型: "open_id" (私聊机器人) 或 "chat_id" (群聊)
RECEIVE_TYPE = os.environ.get("FEISHU_RECEIVE_TYPE", "open_id")

# 目标接收者的 ID
# open_id 模式：默认用 USER_OPEN_ID（用户和机器人的私聊会话）
# chat_id 模式：需要设置 FEISHU_CHAT_ID 环境变量
RECEIVE_ID = os.environ.get("FEISHU_CHAT_ID", USER_OPEN_ID)

# HTML学习卷文件路径
HTML_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "今日学习卷_数学+KET.html")

# 测试卷路径（每科一题）
TEST_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "样式测试卷_每科一题.html")

# ===========================================

def get_token():
    """获取 tenant_access_token"""
    url = "https://open.feishu.cn/open-apis/auth/v3/tenant_access_token/internal"
    resp = requests.post(url, json={"app_id": APP_ID, "app_secret": APP_SECRET}, timeout=10)
    data = resp.json()
    if data.get("code") != 0:
        raise Exception(f"获取token失败: {data}")
    return data["tenant_access_token"]

def send_card_message(token, receive_id, receive_type, title, content):
    """发送富文本卡片消息"""
    url = f"https://open.feishu.cn/open-apis/im/v1/messages?receive_id_type={receive_type}"
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
        "receive_id": receive_id,
        "msg_type": "interactive",
        "content": json.dumps(card)
    }

    resp = requests.post(url, headers=headers, json=body, timeout=15)
    return resp.json()

def upload_file(token, file_path, file_type=None):
    """
    上传文件到飞书，返回 file_key
    file_type: 飞书文件类型 (opus/mp4/pdf/doc/xls/ppt/stream/archive/other)
               不传则根据扩展名自动判断
    """
    url = "https://open.feishu.cn/open-apis/im/v1/files"
    headers = {
        "Authorization": f"Bearer {token}",
    }

    # 根据文件扩展名确定 content_type
    content_type_map = {
        "html": "text/html",
        "htm": "text/html",
        "pdf": "application/pdf",
        "png": "image/png",
        "jpg": "image/jpeg",
        "jpeg": "image/jpeg",
        "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        "xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    }
    ext = os.path.splitext(file_path)[1].lower().lstrip(".")
    ct = content_type_map.get(ext, "application/octet-stream")

    # 飞书 file_type 映射（必须是飞书支持的枚举值）
    feishu_type_map = {
        "html": "stream",
        "htm": "stream",
        "pdf": "pdf",
        "png": "stream",
        "jpg": "stream",
        "jpeg": "stream",
        "docx": "doc",
        "doc": "doc",
        "xlsx": "xls",
        "xls": "xls",
        "pptx": "ppt",
        "ppt": "ppt",
        "zip": "archive",
        "rar": "archive",
        "gz": "archive",
        "tar": "archive",
        "mp4": "mp4",
        "opus": "opus",
    }
    ft = file_type or feishu_type_map.get(ext, "other")

    filename = os.path.basename(file_path)

    with open(file_path, "rb") as f:
        files_data = {"file": (filename, f, ct)}
        form_data = {"file_type": ft, "file_name": filename}
        resp = requests.post(url, headers=headers, files=files_data, data=form_data, timeout=30)

    result = resp.json()
    if result.get("code") != 0:
        raise Exception(f"文件上传失败: {result}")
    file_key = result.get("data", {}).get("file_key")
    print(f"[文件上传] ✅ 成功: {filename} -> file_key={file_key} (type={ft})")
    return file_key


def send_file_message(token, receive_id, receive_type, file_key, file_name="学习卷.html"):
    """发送文件类型消息"""
    url = f"https://open.feishu.cn/open-apis/im/v1/messages?receive_id_type={receive_type}"
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json; charset=utf-8"
    }
    body = {
        "receive_id": receive_id,
        "msg_type": "file",
        "content": json.dumps({"file_key": file_key})
    }
    resp = requests.post(url, headers=headers, json=body, timeout=15)
    return resp.json()


def send_text_message(token, receive_id, receive_type, text):
    """发送纯文本消息（备选方案）"""
    url = f"https://open.feishu.cn/open-apis/im/v1/messages?receive_id_type={receive_type}"
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json; charset=utf-8"
    }
    body = {
        "receive_id": receive_id,
        "msg_type": "text",
        "content": json.dumps({"text": text})
    }
    resp = requests.post(url, headers=headers, json=body, timeout=15)
    return resp.json()

def build_daily_card(math_topics, english_topics, date_str):
    """构建每日学习卷卡片内容"""
    today = date_str or datetime.now().strftime("%m月%d日")

    content = f"**📅 日期：{today}**\n\n"
    content += "**📐 数学专题小卷（满分100分 | 建议用时40分钟）**\n\n"

    for topic in math_topics:
        score = topic.get("score", "")
        content += f"- **{topic['name']}**{f' | {score}分' if score else ''}\n"
        desc = topic.get("desc", "")
        if desc:
            content += f"  *{desc}*\n"

    content += "\n---\n\n"
    content += "**📘 英语KET 学习卷（满分100分 | 建议用时60~90分钟）**\n\n"

    for topic in english_topics:
        score = topic.get("score", "")
        content += f"- **{topic['name']}**{f' | {score}分' if score else ''}\n"
        desc = topic.get("desc", "")
        if desc:
            content += f"  *{desc}*\n"

    content += "\n---\n\n"
    content += "> ✅ 完成后在卷上打分，拍照上传存档\n"
    content += "> 💡 家长评价栏别忘了打分哦！\n"
    content += "> 🖨️ 打开HTML文件可直接打印或导出PDF"

    return content

# 默认学习卷内容模板
DEFAULT_MATH_TOPICS = [
    {"name": "一、口算速算", "score": "20", "desc": "10题直接写出得数"},
    {"name": "二、脱式计算", "score": "18", "desc": "3题写出每一步过程"},
    {"name": "三、轴对称图形", "score": "22", "desc": "判断5个 + 方格画图"},
    {"name": "四、解决问题", "score": "30", "desc": "3道应用题"},
    {"name": "五、小小设计师·思考", "score": "10", "desc": "开放性思考题"},
]

DEFAULT_ENGLISH_TOPICS = [
    {"name": "Part A · 今日必背单词", "score": "15", "desc": "15个单词 + 默写测试"},
    {"name": "Part B · 语法专题", "score": "30", "desc": "一般现在时精讲+填空+翻译"},
    {"name": "Part C · 听力训练", "score": "25", "desc": "选择题+填空(附家长朗读脚本)"},
    {"name": "Part D · 跟读与口语", "score": "15", "desc": "跟读材料+自查表+口头问答"},
    {"name": "Part E · 写作小练", "score": "10", "desc": "My School Art Festival Logo Design"},
    {"name": "家长评价", "score": "5", "desc": "态度/书写/按时完成/主动复习/大声朗读"},
]

def push_daily_file(file_path=None, receive_id=None, receive_type=None):
    """
    直接上传并发送 HTML 学习卷文件到飞书
    Args:
        file_path: 要发送的HTML文件路径（默认用测试卷）
        receive_id: 接收者ID
        receive_type: 接收类型
    """
    rid = receive_id or RECEIVE_ID
    rtype = receive_type or RECEIVE_TYPE
    fpath = file_path or TEST_FILE

    if not os.path.exists(fpath):
        print(f"[错误] 文件不存在: {fpath}")
        return False

    filename = os.path.basename(fpath)
    print(f"[小肥猫学习] 开始发送学习卷文件...")
    print(f"  文件: {filename}")
    print(f"  接收: {rtype} -> {rid}")

    # 1. 获取 token
    token = get_token()
    print("[1/4] ✅ 访问凭证获取成功")

    # 2. 上传文件
    print(f"[2/4] 正在上传文件: {filename} ...")
    try:
        file_key = upload_file(token, fpath)
    except Exception as e:
        print(f"[2/4] ❌ 文件上传失败: {e}")
        return False

    # 3. 发送文件消息
    print(f"[3/4] 正在发送文件消息...")
    result = send_file_message(token, rid, rtype, file_key, filename)

    if result.get("code") == 0:
        msg_id = result.get("data", {}).get("message_id", "N/A")
        print(f"[4/4] ✅ 文件发送成功! message_id: {msg_id}")

        # 4. 追加一条使用说明文字
        hint = (
            "📎 上面的 HTML 文件就是今天的练习卷，下载后：\n"
            "1️⃣ 用浏览器打开即可查看和打印\n"
            "2️⃣ 孩子线下纸上作答\n"
            "3️⃣ 做完后拍照或文字回复答案给我，我来批改解析！\n"
            "💡 手机/电脑/平板都能打开哦~"
        )
        send_text_message(token, rid, rtype, hint)
        return True
    else:
        print(f"[3/4] ⚠️ 文件发送失败: {result.get('msg', result)}")
        return False


def push_daily(receive_id=None, receive_type=None, date_str=None, custom_content=None):
    """
    推送每日学习卷
    Args:
        receive_id: 接收者ID（默认用配置的）
        receive_type: 接收类型 open_id/chat_id（默认用配置的）
        date_str: 日期字符串（默认今天）
        custom_content: 自定义卡片内容JSON字符串（覆盖默认内容）
    """
    rid = receive_id or RECEIVE_ID
    rtype = receive_type or RECEIVE_TYPE

    print(f"[小肥猫学习] 开始推送每日学习卷...")
    print(f"  接收类型: {rtype} -> {rid}")

    # 1. 获取 token
    token = get_token()
    print("[1/3] ✅ 访问凭证获取成功")

    # 2. 构建消息
    today = date_str or datetime.now().strftime("%m月%d日")
    title = f"📚 每日学习卷 · 数学+英语KET · {today}"

    if custom_content:
        content = custom_content
    else:
        content = build_daily_card(DEFAULT_MATH_TOPICS, DEFAULT_ENGLISH_TOPICS, today)

    # 3. 发送卡片消息
    print(f"[2/3] 正在发送卡片消息...")
    result = send_card_message(token, rid, rtype, title, content)

    if result.get("code") == 0:
        msg_id = result.get("data", {}).get("message_id", "N/A")
        print(f"[3/3] ✅ 推送成功! message_id: {msg_id}")
        return True
    else:
        print(f"[3/3] ⚠️ 卡片发送失败: {result.get('msg', result)}")
        # 备选：尝试发送纯文本
        print(f"[备选] 尝试发送纯文本消息...")
        plain_text = (
            f"📚 每日学习卷 · {today}\n\n"
            f"📐 数学(100分/40min)：口算速算 + 脱式计算 + 轴对称图形 + 应用题 + 思考题\n\n"
            f"📘 英语KET(100分/60-90min)：15词 + 一般现在时语法 + 听力 + 口语 + 写作\n\n"
            f"> 完成后打分存档，家长评价别忘记！"
        )
        text_result = send_text_message(token, rid, rtype, plain_text)
        if text_result.get("code") == 0:
            print(f"[3/3] ✅ 纯文本推送成功!")
            return True
        else:
            print(f"[3/3] ❌ 全部失败: {text_result}")
            return False


if __name__ == "__main__":
    # 支持命令行参数
    # 用法:
    #   python3 feishu_push.py                          # 默认：卡片消息
    #   python3 feishu_push.py --file                   # 文件模式（默认测试卷）
    #   python3 feishu_push.py --file /path/to/x.html   # 指定文件
    #   python3 feishu_push.py ou_xxx chat_id           # 指定接收者

    file_mode = False
    file_arg = None
    positional_args = []

    for arg in sys.argv[1:]:
        if arg == "--file":
            file_mode = True
        elif os.path.isfile(arg):
            file_arg = arg
        else:
            positional_args.append(arg)

    if file_mode or file_arg:
        # 文件发送模式
        fpath = file_arg or TEST_FILE
        success = push_daily_file(file_path=fpath)
    else:
        # 卡片/文本消息模式（原有逻辑）
        rid = positional_args[0] if len(positional_args) > 0 else None
        rtype = positional_args[1] if len(positional_args) > 1 else None
        success = push_daily(receive_id=rid, receive_type=rtype)

    sys.exit(0 if success else 1)
