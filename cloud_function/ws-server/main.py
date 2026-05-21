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
import io
import time
import threading
import logging
import base64
import requests
from datetime import datetime, timedelta
from collections import defaultdict
from openai import OpenAI

import lark_oapi as lark
from lark_oapi.api.im.v1.model.p2_im_message_receive_v1 import P2ImMessageReceiveV1

# 导入本地模块
from feishu_api import create_client, send_feishu_card, send_feishu_text, send_feishu_image, upload_feishu_image
from grading import (
    grade_submission,
    grade_submission_multi_image,
    format_grading_card,
    format_partial_grading_card,
    is_command,
    detect_modification_suggestion,
    process_modification_suggestion,
    load_grading_rules,
    check_previous_day_completion,
    get_daily_progress,
    mark_questions_graded,
)

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


# ==================== 图片批次管理 ====================

# 图片批次: {(sender_id, date_str): {"images": [(image_key, image_bytes, ocr_text), ...], "timer": Timer, "msg_ids": []}}
_image_batches = {}
_batch_lock = threading.Lock()
BATCH_WAIT_SECONDS = 60  # 等待新图片的最大秒数


def _start_batch_timer(sender_id: str, msg_date: str):
    """启动/重置批次定时器，超时后自动处理批次"""
    key = (sender_id, msg_date)

    def process():
        time.sleep(BATCH_WAIT_SECONDS)
        with _batch_lock:
            batch = _image_batches.get(key)
            if batch:
                logger.info(f"[批次] 定时器触发，开始处理 {len(batch['images'])} 张图片")
                _process_image_batch(sender_id, msg_date, batch)
                del _image_batches[key]

    timer = threading.Thread(target=process, daemon=True)
    timer.start()

    with _batch_lock:
        if key in _image_batches:
            _image_batches[key]["timer"] = timer


def _process_image_batch(sender_id: str, msg_date: str, batch: dict):
    """处理一个完整的图片批次"""
    images = batch["images"]
    image_count = len(images)

    logger.info(f"[批次处理] sender={sender_id[:12]}... date={msg_date} images={image_count}")

    # 1️⃣ 逐日前置检查
    prev_check = check_previous_day_completion(msg_date)
    if not prev_check.get("can_proceed", True):
        msg = (
            f"⛔ **每日练习规则**\n\n"
            f"📅 你必须先完成 **{prev_check['prev_date']}** 的练习才能开始 {msg_date} 的！\n\n"
        )
        if prev_check.get("remaining_questions"):
            msg += f"📋 **{prev_check['prev_date']} 剩余题目**：\n"
            for q in prev_check["remaining_questions"]:
                msg += f"  • {q.get('id', '?')} - {q.get('content', '')[:60]}...\n"
            msg += "\n"
        msg += f"✅ 完成后发送答案即可自动解锁今天的练习。"
        send_feishu_text(fs_client, sender_id, msg)
        return

    # 2️⃣ 发送处理中提示
    send_feishu_text(
        fs_client, sender_id,
        f"📸 收到 {image_count} 张图片，正在识别中...\n"
        f"🐱 小肥猫会逐张识别并匹配对应的题目区域。"
    )

    # 3️⃣ 逐张 OCR + 汇总
    all_ocr_results = []
    for i, img_tuple in enumerate(images):
        # 兼容 3 元素和 4 元素元组
        if len(img_tuple) >= 4:
            img_key, img_bytes, _, content_type = img_tuple[:4]
        else:
            img_key, img_bytes, _ = img_tuple[:3]
            content_type = "image/jpeg"
        try:
            ocr_text = _ocr_image(img_bytes)
            all_ocr_results.append({
                "index": i + 1,
                "image_key": img_key,
                "ocr_text": ocr_text,
            })
            logger.info(f"[OCR-{i+1}] 图片{i+1} 识别: {ocr_text[:80]}...")
        except Exception as e:
            logger.error(f"[OCR-{i+1}] 识别失败: {e}")
            all_ocr_results.append({
                "index": i + 1,
                "image_key": img_key,
                "ocr_text": "",
                "error": str(e)[:100],
            })

    # 4️⃣ 合并 OCR 结果
    combined_text_parts = []
    for r in all_ocr_results:
        if r.get("ocr_text"):
            combined_text_parts.append(f"[图{r['index']}] {r['ocr_text']}")
    combined_text = "\n".join(combined_text_parts)

    if not combined_text.strip():
        send_feishu_text(fs_client, sender_id, "⚠️ 未能从图片中识别到答案，请拍照更清晰后重试。")
        return

    # 5️⃣ 多图片区段匹配 + 部分批改
    logger.info(f"[批次处理] 开始区段匹配批改，共 {len(combined_text_parts)} 段OCR结果")
    result = grade_submission_multi_image(
        fs_client, combined_text, msg_date,
        all_ocr_results, image_keys=[r["image_key"] for r in all_ocr_results]
    )

    # 6️⃣ 输出结果
    if result["success"]:
        title, content = format_partial_grading_card(result)
        send_feishu_card(fs_client, sender_id, title, content)

        # 通知剩余题目
        if result.get("remaining_questions"):
            remaining = result["remaining_questions"]
            remaining_msg = f"📋 **还有 {len(remaining)} 道题未作答**，拍照发送即可继续批改：\n"
            for q in remaining[:5]:
                remaining_msg += f"  • {q.get('id', '?')} - {q.get('content', '')[:50]}...\n"
            if len(remaining) > 5:
                remaining_msg += f"  ... 等 {len(remaining)} 道题\n"
            send_feishu_text(fs_client, sender_id, remaining_msg)
            # 标记已批改的题目
            graded_ids = [d["question"]["id"] for d in result.get("details", [])]
            mark_questions_graded(msg_date, graded_ids)

        logger.info(
            f"[批次完成] {result['correct_count']}✓/{result.get('partial_count',0)}🔶/{result['wrong_count']}✗ "
            f"得分率 {result['pass_rate']}% 已完成 {result.get('graded_count',0)}/{result.get('total_questions',0)}"
        )
    else:
        send_feishu_text(fs_client, sender_id, result.get("summary", "批改未成功，请检查图片内容"))
        logger.info(f"[批次失败] {result.get('summary','')[:80]}")


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


def _download_image(image_key: str) -> tuple:
    """
    从飞书下载图片。
    v2.2 修复：复用 feishu_api 的 token 缓存，避免每次请求都获取新 token；
    同时返回图片二进制数据和 Content-Type 供上传时复用。

    Returns: (image_bytes: bytes, content_type: str)
    """
    from feishu_api import _get_tenant_token

    from urllib.parse import quote

    token = _get_tenant_token()
    # 飞书图片下载 API：image_key 需 URL 编码（可能含特殊字符），image_type=message 表示消息图片
    url = f"https://open.feishu.cn/open-apis/im/v1/images/{quote(image_key, safe='')}?image_type=message"
    headers = {"Authorization": f"Bearer {token}"}
    logger.info(f"[下载图片] URL={url[:100]}...")
    resp = requests.get(url, headers=headers, timeout=30)

    if resp.status_code != 200:
        logger.error(f"[下载失败] HTTP {resp.status_code}: {resp.text[:200]}")
        resp.raise_for_status()

    # 飞书图片下载 API 直接返回二进制图片数据（Content-Type: image/*）
    content_type = resp.headers.get("Content-Type", "image/jpeg")
    if "image" in content_type:
        return resp.content, content_type

    # 非图片响应（如 JSON 错误信息），尝试解析检查
    try:
        data = resp.json()
        if data.get("code") != 0:
            raise Exception(f"下载图片失败: {data.get('msg')}")
    except (json.JSONDecodeError, ValueError):
        pass  # 无法解析也为二进制，直接返回

    return resp.content, content_type


# ==================== OCR 图片预处理 ====================

# 常见 OCR 识别错误纠正表（中文同形字/形近字混淆）
_OCR_CORRECTIONS = {
    "捅": "桶", "铜": "桶", "简": "筒",
    "千克": "千克",  # 防止被替换
    "干克": "千克", "十克": "千克",
    "干米": "千米", "午米": "千米",
    "屋米": "厘米", "厘来": "厘米", "厦米": "厘米",
    "分来": "分米", "分米": "分米",
    "寒来": "毫米", "亳米": "毫米",
    "平方干米": "平方千米", "立方米": "立方米",
    "平万米": "平方米", "平米": "平方米",
    "干吨": "千吨", "午吨": "千吨",
    "竞然": "竟然", "井且": "并且",
    "左石": "左右", "大约": "大约",
    "正确": "正确", "辅误": "错误",
    "不变": "不变", "平移": "平移",
    "旋传": "旋转", "对称": "对称",
    "倒如": "例如", "相以": "相似",
    "因比": "因此", "所以": "所以",
    "日": "日",  # 保留日期用
}

# 多轮 OCR 后可调用 DeepSeek 做语义校验的关键词
_VISION_VERIFY_TRIGGER = ["千克", "千米", "平移", "旋转", "对称", "不变"]


def _preprocess_for_ocr(image_bytes: bytes, min_short_side: int = 800) -> bytes:
    """
    图片预处理：提高 OCR 识别率
    - 自动缩放（短边不足 800px 时放大）
    - 灰度化 + 自适应对比度增强
    - 锐化
    - 可选去噪

    Returns: 处理后的 JPEG bytes（适合 OCR）
    """
    try:
        from PIL import Image, ImageEnhance, ImageFilter

        img = Image.open(io.BytesIO(image_bytes))

        # 如果图片是 RGBA，转为 RGB
        if img.mode == "RGBA":
            background = Image.new("RGB", img.size, (255, 255, 255))
            background.paste(img, mask=img.split()[3])
            img = background
        elif img.mode != "RGB":
            img = img.convert("RGB")

        w, h = img.size
        short_side = min(w, h)
        long_side = max(w, h)

        # === 1. 自适应缩放 ===
        if short_side < min_short_side:
            scale = min_short_side / short_side
            # 避免过长边超出合理范围（3200px）
            if long_side * scale > 3200:
                scale = 3200 / long_side
            new_w, new_h = int(w * scale), int(h * scale)
            img = img.resize((new_w, new_h), Image.LANCZOS)
            logger.info(f"[预处理] 缩放: {w}x{h} → {new_w}x{new_h} (scale={scale:.1f}x)")

        # === 2. 对比度增强 ===
        enhancer = ImageEnhance.Contrast(img)
        img = enhancer.enhance(1.3)  # 提高 30% 对比度

        # === 3. 锐化 ===
        img = img.filter(ImageFilter.SHARPEN)

        # === 4. 输出为 JPEG ===
        buf = io.BytesIO()
        img.save(buf, format="JPEG", quality=92)
        processed = buf.getvalue()

        logger.info(f"[预处理] 完成: {len(image_bytes)}→{len(processed)} bytes")
        return processed

    except ImportError:
        logger.warning("[预处理] Pillow 不可用，跳过预处理")
        return image_bytes
    except Exception as e:
        logger.warning(f"[预处理] 失败: {e}，使用原图")
        return image_bytes


def _correct_ocr_text(text: str) -> str:
    """
    OCR 结果智能纠错：修复常见中文形近字混淆
    例如：干克 → 千克，屋米 → 厘米，旋传 → 旋转
    """
    if not text:
        return text

    result = text
    for wrong, correct in _OCR_CORRECTIONS.items():
        if wrong != correct and wrong in result:
            result = result.replace(wrong, correct)
            logger.info(f"[OCR纠错] {wrong} → {correct}")

    return result


def _ocr_image(image_bytes: bytes) -> str:
    """
    从图片提取答案文本 — 增强版。
    1️⃣ 原图 → 飞书 OCR
    2️⃣ 预处理图 → 飞书 OCR（结果合并去重）
    3️⃣ 降级 → DeepSeek Vision
    4️⃣ 结果智能纠错
    """
    from feishu_api import ocr_image_feishu

    collected_lines = []  # 用 list 保持顺序，去重
    seen = set()

    def add_lines(lines):
        for line in lines:
            stripped = line.strip()
            if stripped and stripped not in seen:
                seen.add(stripped)
                collected_lines.append(stripped)

    # ===== 1️⃣ 原图 → 飞书 OCR =====
    text_lines_1 = ocr_image_feishu(image_bytes)
    add_lines(text_lines_1)
    logger.info(f"[OCR-原图] 飞书OCR → {len(text_lines_1)} 行")

    # ===== 2️⃣ 预处理图 → 飞书 OCR（第二遍，捕捉遗漏） =====
    preprocessed = _preprocess_for_ocr(image_bytes)
    if preprocessed != image_bytes:
        text_lines_2 = ocr_image_feishu(preprocessed)
        new_count = sum(1 for l in text_lines_2 if l.strip() not in seen)
        add_lines(text_lines_2)
        logger.info(f"[OCR-增强] 飞书OCR → {len(text_lines_2)} 行（新增 {new_count} 行）")

    ocr_result = "\n".join(collected_lines)

    # ===== 3️⃣ 智能纠错 =====
    corrected = _correct_ocr_text(ocr_result)
    if corrected != ocr_result:
        logger.info(f"[OCR纠错] 应用纠错规则")

    # ===== 4️⃣ 如果结果太少，尝试 DeepSeek Vision =====
    if len(corrected) < 10 and DEEPSEEK_API_KEY:
        logger.info("[OCR] 飞书OCR结果稀疏，尝试 DeepSeek Vision")
        try:
            client = OpenAI(api_key=DEEPSEEK_API_KEY, base_url=DEEPSEEK_BASE_URL)
            b64 = base64.b64encode(preprocessed).decode()
            resp = client.chat.completions.create(
                model="deepseek-chat",
                messages=[{
                    "role": "user",
                    "content": [
                        {"type": "text", "text": (
                            "这是一张小学生作业答案图片（数学或英语）。请做三件事：\n"
                            "1. 仔细观察图片中所有手写或印刷的文字\n"
                            "2. 提取所有答案内容，按从上到下、从左到右的顺序输出\n"
                            "3. 特别注意手写的中文、数字和数学符号，不要遗漏\n"
                            "如果图片中有题号（如 1. 2. 3.），请保留题号。\n"
                            "只输出识别到的内容，不要解释或点评。"
                        )},
                        {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{b64}"}},
                    ]
                }],
                max_tokens=800,
            )
            vision_result = resp.choices[0].message.content.strip()
            logger.info(f"[OCR-Vision] DeepSeek: {vision_result[:100]}")
            # 合并：如果 Vision 结果明显更丰富，替换；否则追加
            if len(vision_result) > len(corrected) * 1.5:
                corrected = vision_result
            else:
                corrected += "\n" + vision_result
        except Exception as e:
            logger.warning(f"[OCR-Vision] 降级失败: {e}")

    logger.info(f"[OCR完成] 最终结果 ({len(corrected)} 字符): {corrected[:120]}")
    return corrected.strip()


def do_p2_im_message_receive_v1(data: P2ImMessageReceiveV1) -> None:
    """
    处理飞书消息事件（WebSocket 推送）
    支持 text / post / image 三种消息类型

    v2.2 新增：
    - 多图片批次收集（60秒内图片归入同一批次统一处理）
    - 逐日前置完成检查
    - 区段匹配部分批改
    """
    try:
        event = data.event
        message = event.message

        # 提取发送者 open_id
        sender_id = event.sender.sender_id.open_id or USER_OPEN_ID
        msg_timestamp = int(message.create_time) if message.create_time else 0
        msg_date = datetime.fromtimestamp(msg_timestamp / 1000).strftime("%Y-%m-%d")

        # ========== 1️⃣ 图片消息：加入批次收集 ==========
        if message.message_type == "image":
            msg_content = json.loads(message.content)
            image_key = msg_content.get("image_key", "")
            logger.info(f"[图片] sender={sender_id[:12]}... image_key={image_key[:30]}...")

            if not image_key:
                return

            # 下载图片
            send_feishu_text(fs_client, sender_id, f"📸 收到图片，正在加入批次...（{BATCH_WAIT_SECONDS}秒内可继续发图）")
            try:
                image_bytes, content_type = _download_image(image_key)
            except Exception as e:
                logger.error(f"[下载失败] {e}")
                send_feishu_text(fs_client, sender_id, f"⚠️ 图片下载失败: {str(e)[:80]}")
                return

            # 加入批次（保留 content_type 供后续上传复用）
            batch_key = (sender_id, msg_date)
            with _batch_lock:
                if batch_key not in _image_batches:
                    _image_batches[batch_key] = {"images": [], "timer": None}
                    # 立即启动定时器（在锁外也启动一次确保定时器存在）
                _image_batches[batch_key]["images"].append((image_key, image_bytes, msg_date, content_type))
                batch_size = len(_image_batches[batch_key]["images"])
                is_new_batch = batch_size == 1
                logger.info(f"[批次] 当前批次有 {batch_size} 张图片")

            # 首次图片启动定时器
            if is_new_batch:
                _start_batch_timer(sender_id, msg_date)
            else:
                # 通知用户已加入批次
                send_feishu_text(
                    fs_client, sender_id,
                    f"📸 第 {batch_size} 张图片已加入批次（共{len(_image_batches.get(batch_key, {}).get('images',[]))}张）。\n"
                    f"⏳ 将在收到最后一张图后 {BATCH_WAIT_SECONDS} 秒自动处理。"
                )

            return  # 图片消息不继续走下面的文本处理流程

        # ========== 2️⃣ 文本消息：正常处理 ==========
        text = _extract_message_text(message)
        if not text:
            logger.info(f"忽略非文本/非图片消息: {message.message_type}")
            return

        if not text:
            return

        image_key = ""  # 文本消息无 image_key
        logger.info(f"[消息] sender={sender_id[:12]}... type={message.message_type} text={text[:80]}")

        # 3️⃣ 修改建议检测（优先于指令检测）
        if detect_modification_suggestion(text):
            logger.info(f"[修改建议] 检测到规则修改建议: {text[:60]}")
            send_feishu_text(fs_client, sender_id, "🐱 正在解析修改建议...")
            try:
                result = process_modification_suggestion(text)
                if result.get("success"):
                    send_feishu_text(
                        fs_client, sender_id,
                        f"✅ 规则已更新！\n\n"
                        f"📝 {result.get('message', '')}\n"
                        f"💡 {result.get('explanation', '')}\n\n"
                        f"下次批改将自动应用新规则。\n"
                        f"发送「查看规则」可查看所有当前规则。"
                    )
                    logger.info(f"[修改建议] 处理成功: {result.get('action')}")
                else:
                    send_feishu_text(
                        fs_client, sender_id,
                        f"⚠️ 规则调整未生效\n\n{result.get('message', '')}\n\n"
                        f"💡 提示：请用更明确的语言，例如：\n"
                        f"「调整：翻译题意思对即可」\n"
                        f"「修改：数学答案用中文写也算对」\n"
                        f"「新增规则：单词拼写差1个字母不扣全分」"
                    )
            except Exception as e:
                logger.error(f"[修改建议] 处理异常: {e}")
                send_feishu_text(
                    fs_client, sender_id,
                    f"⚠️ 规则调整失败: {str(e)[:100]}\n请稍后重试或直接编辑 grading_rules.json"
                )
            return

        # 3.8️⃣ 发送今日题目指令
        if any(kw in text for kw in ["今日题目", "重新发题目", "发题目", "题目列表", "今天题目", "今天的题"]):
            logger.info("[指令] 发送今日题目")
            try:
                from feishu_api import bitable_list_records
                today = datetime.now().strftime("%Y-%m-%d")
                filter_str = f'CurrentValue.[日期] = "{today}"'
                records = bitable_list_records(fs_client, BITABLE_APP_TOKEN, BITABLE_DAILY_TABLE_ID, filter_str)

                if not records:
                    send_feishu_text(fs_client, sender_id, f"📭 {today} 还没有题目记录，请等待每日出题推送。")
                    return

                # 按科目分组
                math_qs = []
                eng_qs = []
                for rec in records:
                    f = rec.fields or {}
                    q = {
                        "id": f.get("题号", ""),
                        "type": f.get("题型", ""),
                        "content": f.get("题目内容", ""),
                        "score": f.get("分值", ""),
                        "subject": f.get("科目", ""),
                    }
                    if q["subject"] == "数学":
                        math_qs.append(q)
                    else:
                        eng_qs.append(q)

                title = f"📝 今日题目 · {today}"
                content = f"📅 **{today} 学习卷** 共 {len(records)} 道题\n\n"

                if math_qs:
                    content += "📐 **数学**\n"
                    for q in math_qs:
                        content += f"• **{q['id']}** [{q['type']}] ({q['score']}分)\n"
                        content += f"  {q['content'][:120]}\n\n"

                if eng_qs:
                    content += "📘 **英语 / KET**\n"
                    for q in eng_qs:
                        content += f"• **{q['id']}** [{q['type']}] ({q['score']}分)\n"
                        content += f"  {q['content'][:120]}\n\n"

                content += "---\n"
                content += "💡 发送答案格式：\n"
                content += "• 直接回复答案（如 `83 44 forget arrive`）\n"
                content += "• 或拍照发图自动识别\n"
                content += "🐱 小肥猫会自动识别题目并批改！"

                send_feishu_card(fs_client, sender_id, title, content)
                logger.info(f"[题目发送] 已发送 {len(records)} 道题 ({len(math_qs)}数/{len(eng_qs)}英)")
            except Exception as e:
                logger.error(f"[题目发送] 失败: {e}")
                send_feishu_text(fs_client, sender_id, f"⚠️ 获取题目失败: {str(e)[:100]}")
            return

        # 4️⃣ 查看规则 + 指令拦截
        if "查看规则" in text or "规则列表" in text or "所有规则" in text:
            logger.info("[指令] 查看规则")
            rules = load_grading_rules()
            if rules:
                rules_text = "📋 **当前批改规则**\n\n"
                for i, r in enumerate(rules, 1):
                    subject_map = {"all": "全科", "数学": "📐数学", "英语": "📘英语"}
                    subject_label = subject_map.get(r.get("subject", "all"), r.get("subject", ""))
                    rules_text += f"{i}. [{subject_label}] {r.get('rule', '')}\n"
                rules_text += "\n💡 回复「调整：XXX」即可修改规则"
                send_feishu_text(fs_client, sender_id, rules_text)
            else:
                send_feishu_text(fs_client, sender_id, "📋 暂无自定义规则（使用默认批改标准）")
            return

        # 4.5️⃣ 查看进度指令
        if "查看进度" in text or "完成情况" in text or "今日进度" in text:
            logger.info("[指令] 查看进度")
            progress = get_daily_progress(msg_date)
            if progress:
                total = progress.get("total_questions", 0)
                graded = progress.get("graded_question_ids", [])
                msg = f"📊 **{msg_date} 练习进度**\n\n"
                msg += f"📝 总题数：{total} 道\n"
                msg += f"✅ 已完成：{len(graded)} 道\n"
                msg += f"⏳ 待完成：{total - len(graded)} 道\n"
                if total > len(graded):
                    msg += f"\n继续发送答案或拍照即可完成剩余题目！"
                else:
                    msg += f"\n🎉 今日练习已全部完成！"
                send_feishu_text(fs_client, sender_id, msg)
            else:
                send_feishu_text(fs_client, sender_id, f"📭 {msg_date} 暂无练习数据，请等待每日出题推送。")
            return

        # 5️⃣ 指令拦截
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

        # 5.5️⃣ 逐日前置检查（文本批改也需检查）
        prev_check = check_previous_day_completion(msg_date)
        if not prev_check.get("can_proceed", True):
            msg = (
                f"⛔ **每日练习规则**\n\n"
                f"📅 你必须先完成 **{prev_check['prev_date']}** 的练习才能开始 {msg_date} 的！\n\n"
            )
            if prev_check.get("remaining_questions"):
                msg += f"📋 **{prev_check['prev_date']} 剩余题目**：\n"
                for q in prev_check["remaining_questions"]:
                    msg += f"  • {q.get('id', '?')} - {q.get('content', '')[:60]}...\n"
                msg += "\n"
            msg += f"✅ 完成后发送答案即可自动解锁今天的练习。"
            send_feishu_text(fs_client, sender_id, msg)
            return

        # 6️⃣ 执行批改
        logger.info("[批改] 开始处理...")
        result = grade_submission(fs_client, text, msg_date, image_key)

        if result["success"]:
            title, content = format_grading_card(result)
            send_feishu_card(fs_client, sender_id, title, content)

            # 记录进度
            graded_ids = [d["question"]["id"] for d in result.get("details", [])]
            mark_questions_graded(msg_date, graded_ids)

            logger.info(
                f"[批改完成] {result['correct_count']}✓/{result.get('partial_count',0)}🔶/{result['wrong_count']}✗ "
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
