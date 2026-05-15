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
from datetime import datetime

import lark_oapi as lark
from lark_oapi.api.im.v1.model.p2_im_message_receive_v1 import P2ImMessageReceiveV1

# 导入本地模块
from feishu_api import create_client, send_feishu_card, send_feishu_text
from grading import grade_submission, format_grading_card, is_command

# ==================== 配置 ====================

APP_ID = os.environ.get("FEISHU_APP_ID", "")
APP_SECRET = os.environ.get("FEISHU_APP_SECRET", "")
USER_OPEN_ID = os.environ.get("USER_OPEN_ID", "ou_8bf3770ed43ce0f273c7a34f1597cfe9")

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


def do_p2_im_message_receive_v1(data: P2ImMessageReceiveV1) -> None:
    """
    处理飞书消息事件（WebSocket 推送）
    对应事件类型：im.message.receive_v1
    """
    try:
        event = data.event
        message = event.message

        # 提取消息文字内容（支持 text 和 post 两种类型）
        text = _extract_message_text(message)
        if not text:
            logger.info(f"忽略非文本消息: {message.message_type}")
            return

        if not text:
            return

        # 提取发送者 open_id
        sender_id = event.sender.sender_id.open_id or USER_OPEN_ID

        # 获取消息时间
        msg_timestamp = int(message.create_time) if message.create_time else 0
        msg_date = datetime.fromtimestamp(msg_timestamp / 1000).strftime("%Y-%m-%d")

        logger.info(f"[消息] sender={sender_id[:12]}... type={message.message_type} text={text[:80]}")

        # 指令处理：友好回复而非静默跳过
        if is_command(text):
            logger.info(f"[指令] 识别为指令: {text[:50]}")
            send_feishu_text(
                fs_client, sender_id,
                f"🐱 收到指令「{text[:30]}」\n\n"
                f"当前长连接批改服务仅支持**答案提交批改**功能。\n"
                f"如需出题、错题本等功能，请使用 Mac 本地的 bot_server。\n\n"
                f"💡 发送答案（如 83 44 63,22 forget arrive plan）即可批改。"
            )
            return

        # 执行批改
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
            send_feishu_text(fs_client, sender_id, result.get("summary", "批改未成功，请稍后重试"))
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
