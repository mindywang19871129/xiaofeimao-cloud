#!/usr/bin/env python3
"""
小肥猫学习 - WebSocket 长连接模式
=================================
使用飞书 WebSocket 长连接接收事件，无需公网 URL。
部署：云服务器常驻进程 python main.py

飞书后台配置：
  - 事件与回调 → 订阅方式 → "使用长连接接收事件"
  - 订阅事件：im.message.receive_v1（接收消息）
"""

import os
import sys
import json
import logging
import base64
import requests
from datetime import datetime
from openai import OpenAI

import lark_oapi as lark
from lark_oapi.api.im.v1.model.p2_im_message_receive_v1 import P2ImMessageReceiveV1

# 导入本地模块
from feishu_api import create_client, send_feishu_card, send_feishu_text
from grading import grade_submission, format_grading_card, is_command

# ==================== 配置 ====================

APP_ID = os.environ.get("FEISHU_APP_ID", "")
APP_SECRET = os.environ.get("FEISHU_APP_SECRET", "")
USER_OPEN_ID = os.environ.get("USER_OPEN_ID", "ou_8bf3770ed43ce0f273c7a34f1597cfe9")
DEEPSEEK_API_KEY = os.environ.get("DEEPSEEK_API_KEY", "")
DEEPSEEK_BASE_URL = "https://api.deepseek.com"

# 日志
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("xiaofeimao")

# ==================== 初始化 ====================

# 创建飞书 SDK 客户端（用于 API 调用：bitable、发消息等）
fs_client = create_client(APP_ID, APP_SECRET)


# ==================== 事件处理 ====================

def _extract_message_text(message) -> str:
    """
    从飞书消息中提取纯文本内容
    支持 message_type: text / post
    """
    msg_content = json.loads(message.content)

    if message.message_type == "text":
        return msg_content.get("text", "").strip()

    if message.message_type == "post":
        # post 消息结构: content 是 JSON 字符串，含 title 和 content 数组
        # content 是 [[{tag, text}, ...], ...] 的多段落结构
        try:
            post_content = json.loads(message.content) if isinstance(message.content, str) else message.content
            parts = []
            # 提取标题
            title = post_content.get("title", "")
            if title:
                parts.append(title)
            # 提取各段落文本
            for paragraph in post_content.get("content", []):
                for elem in paragraph:
                    if elem.get("tag") == "text":
                        parts.append(elem.get("text", ""))
                    elif elem.get("tag") == "at":
                        # @人的文本
                        parts.append(elem.get("user_name", ""))
            return " ".join(parts).strip()
        except (json.JSONDecodeError, TypeError, KeyError):
            # 降级: 把整个 content 当字符串
            return str(message.content).strip()

    # 其他类型
    return ""


def _download_image(image_key: str) -> bytes:
    """从飞书下载图片"""
    token_resp = fs_client.auth.v3.tenant_access_token.internal.create(
        lark.auth.v3.CreateTenantAccessTokenReq(
            body={"app_id": APP_ID, "app_secret": APP_SECRET}
        )
    )
    token = token_resp.data.tenant_access_token
    url = f"https://open.feishu.cn/open-apis/im/v1/images/{image_key}"
    headers = {"Authorization": f"Bearer {token}"}
    resp = requests.get(url, headers=headers, timeout=30)
    resp.raise_for_status()
    data = resp.json()
    if data.get("code") != 0:
        raise Exception(f"下载图片失败: {data.get('msg')}")
    return resp.content


def _ocr_image(image_bytes: bytes) -> str:
    """用 DeepSeek Vision 从图片提取答案文本"""
    if not DEEPSEEK_API_KEY:
        return ""
    client = OpenAI(api_key=DEEPSEEK_API_KEY, base_url=DEEPSEEK_BASE_URL)
    b64 = base64.b64encode(image_bytes).decode()
    resp = client.chat.completions.create(
        model="deepseek-chat",
        messages=[{
            "role": "user",
            "content": [
                {"type": "text", "text": "这是一张作业答案图片。请提取所有答案，按题号顺序输出，每道题答案用空格分隔。如有多选或填空多空，用逗号分隔。只输出答案，不要解释。"},
                {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{b64}"}},
            ]
        }],
        max_tokens=500,
    )
    return resp.choices[0].message.content.strip()


def do_p2_im_message_receive_v1(data: P2ImMessageReceiveV1) -> None:
    """
    处理飞书消息事件（WebSocket 推送）
    支持 text / post / image 三种消息类型
    """
    try:
        event = data.event
        message = event.message

        # 提取发送者 open_id
        sender_id = event.sender.sender_id.open_id or USER_OPEN_ID
        msg_timestamp = int(message.create_time) if message.create_time else 0
        msg_date = datetime.fromtimestamp(msg_timestamp / 1000).strftime("%Y-%m-%d")

        # 1️⃣ 处理图片消息：下载 → OCR → 提取答案
        if message.message_type == "image":
            msg_content = json.loads(message.content)
            image_key = msg_content.get("image_key", "")
            logger.info(f"[图片] sender={sender_id[:12]}... image_key={image_key[:20]}...")

            if not image_key:
                return

            send_feishu_text(fs_client, sender_id, "🔍 正在识别图片中的答案...")
            try:
                image_bytes = _download_image(image_key)
                text = _ocr_image(image_bytes)
                logger.info(f"[OCR] 识别结果: {text[:100]}")
            except Exception as e:
                logger.error(f"[OCR失败] {e}")
                send_feishu_text(fs_client, sender_id, f"⚠️ 图片识别失败: {str(e)[:80]}")
                return

            if not text:
                send_feishu_text(fs_client, sender_id, "⚠️ 未能从图片中识别到答案文本，请拍照更清晰后重试。")
                return

        # 2️⃣ 处理文本消息
        else:
            text = _extract_message_text(message)
            if not text:
                logger.info(f"忽略非文本/非图片消息: {message.message_type}")
                return

        if not text:
            return

        logger.info(f"[消息] sender={sender_id[:12]}... type={message.message_type} text={text[:80]}")

        # 3️⃣ 指令拦截
        if is_command(text):
            logger.info(f"[指令] 识别为指令: {text[:50]}")
            send_feishu_text(
                fs_client, sender_id,
                f"🐱 收到「{text[:20]}」\n\n发送答案即可自动批改，支持以下格式：\n"
                f"• `83 44 63,22 forget arrive` （空格分隔）\n"
                f"• `|1|83| |2|44| |3|63,22|` （管道格式）\n"
                f"• `M1=83 M2=44 E1=important,taller` （标签格式）\n"
                f"• 📷 直接拍照发图"
            )
            return

        # 4️⃣ 执行批改
        logger.info("[批改] 开始处理...")
        result = grade_submission(fs_client, text, msg_date)

        if result["success"]:
            title, content = format_grading_card(result)
            send_feishu_card(fs_client, sender_id, title, content)
            logger.info(
                f"[批改完成] {result['correct_count']}✓/{result['wrong_count']}✗ "
                f"得分率 {result['pass_rate']}%"
            )
        else:
            send_feishu_text(fs_client, sender_id, result.get("summary", "批改未成功，请检查答案格式"))
            logger.info(f"[批改失败] {result.get('summary','')[:80]}")

    except Exception as e:
        logger.error(f"[异常] 事件处理失败: {e}", exc_info=True)
        try:
            send_feishu_text(
                fs_client,
                USER_OPEN_ID,
                f"⚠️ 批改遇到问题: {str(e)[:100]}\n请稍后重试或联系管理员。",
            )
        except Exception:
            pass


# ==================== 主入口 ====================

def main():
    # 检查必要的环境变量
    if not APP_ID or not APP_SECRET:
        logger.error("❌ 缺少 FEISHU_APP_ID 或 FEISHU_APP_SECRET 环境变量")
        sys.exit(1)

    logger.info("=" * 50)
    logger.info("🐱 小肥猫学习 - WebSocket 长连接批改服务")
    logger.info(f"   App ID: {APP_ID[:8]}...")
    logger.info("   模式: WebSocket 长连接（无需公网 URL）")
    logger.info("=" * 50)

    # 注册事件处理器
    event_handler = (
        lark.EventDispatcherHandler.builder("", "")
        .register_p2_im_message_receive_v1(do_p2_im_message_receive_v1)
        .build()
    )

    # 创建 WebSocket 客户端并启动
    cli = lark.ws.Client(
        APP_ID,
        APP_SECRET,
        event_handler=event_handler,
        log_level=lark.LogLevel.INFO,
    )

    logger.info("🔗 正在建立 WebSocket 长连接到飞书...")
    logger.info("   请在飞书后台 → 事件与回调 → 选择「使用长连接接收事件」")

    try:
        cli.start()  # 阻塞运行，保持连接
    except KeyboardInterrupt:
        logger.info("🛑 收到退出信号，服务关闭")
    except Exception as e:
        logger.error(f"❌ 连接异常: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
