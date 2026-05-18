"""
飞书 API 封装层
================
使用 lark-oapi SDK 封装飞书接口调用。
SDK 自动管理 tenant_access_token，无需手动获取和缓存。

v2.2 新增：
  - upload_feishu_image(): 上传图片到飞书获取 image_key
  - send_feishu_image(): 发送图片消息
  - 图片上传使用 HTTP raw request（绕过 SDK，更可靠）
"""

import os
import io
import json
import time
import logging
from typing import Optional

import requests
import lark_oapi as lark
from lark_oapi.api.bitable.v1 import (
    ListAppTableRecordRequest,
    CreateAppTableRecordRequest,
    AppTableRecord,
)
from lark_oapi.api.im.v1 import (
    CreateMessageRequest,
    CreateMessageRequestBody,
)

logger = logging.getLogger("feishu_api")

# 图片上传/下载用的 app credentials（从环境变量读取，与 SDK 共享）
_APP_ID = os.environ.get("FEISHU_APP_ID", "")
_APP_SECRET = os.environ.get("FEISHU_APP_SECRET", "")

# token 缓存（HTTP 方式）
_token_cache = {"token": "", "expires_at": 0}


def _get_tenant_token() -> str:
    """获取 tenant_access_token（HTTP 方式，带缓存）"""
    global _token_cache
    now = time.time()
    if _token_cache["token"] and _token_cache["expires_at"] > now + 60:
        return _token_cache["token"]

    url = "https://open.feishu.cn/open-apis/auth/v3/tenant_access_token/internal"
    resp = requests.post(url, json={
        "app_id": _APP_ID,
        "app_secret": _APP_SECRET,
    }, timeout=15)
    resp.raise_for_status()
    data = resp.json()
    code = data.get("code", -1)
    if code != 0:
        raise Exception(f"获取 tenant_token 失败: code={code}, msg={data.get('msg')}")

    token = data["tenant_access_token"]
    expire = data.get("expire", 7200)
    _token_cache = {"token": token, "expires_at": now + expire}
    logger.debug(f"飞书 token 已刷新 (expire in {expire}s)")
    return token


def _detect_image_info(image_bytes: bytes) -> tuple:
    """
    检测图片的实际格式和 MIME 类型。
    通过文件头魔数判断，不依赖扩展名。

    Returns: (extension_without_dot, mime_type)
    """
    if len(image_bytes) < 4:
        return ("jpg", "image/jpeg")

    header = image_bytes[:4]
    # PNG: 89 50 4E 47
    if header[:4] == b'\x89PNG':
        return ("png", "image/png")
    # JPEG: FF D8 FF
    if header[:2] == b'\xff\xd8':
        return ("jpg", "image/jpeg")
    # GIF: 47 49 46 38
    if header[:3] == b'GIF':
        return ("gif", "image/gif")
    # WebP: 52 49 46 46 ... 57 45 42 50
    if header[:4] == b'RIFF' and len(image_bytes) >= 12 and image_bytes[8:12] == b'WEBP':
        return ("webp", "image/webp")
    # BMP: 42 4D
    if header[:2] == b'BM':
        return ("bmp", "image/bmp")

    # 默认回退 JPEG
    return ("jpg", "image/jpeg")


def upload_feishu_image(image_bytes: bytes, image_type: str = "message") -> Optional[str]:
    """
    上传图片到飞书，获取 image_key。
    用于后续发送图片消息。

    API: POST https://open.feishu.cn/open-apis/im/v1/images
    Content-Type: multipart/form-data
    参数: image_type (str), image (file)

    v2.2 修复：自动检测图片格式（PNG/JPEG/GIF/WebP/BMP），
    不再硬编码 image/jpeg，避免因格式不匹配导致的 400 错误。

    Returns: image_key (str) 或 None
    """
    token = _get_tenant_token()
    url = "https://open.feishu.cn/open-apis/im/v1/images"
    headers = {"Authorization": f"Bearer {token}"}

    # 自动检测图片格式
    ext, mime = _detect_image_info(image_bytes)
    filename = f"image.{ext}"
    logger.debug(f"图片格式检测: {mime} (文件名: {filename})")

    files = {
        "image_type": (None, image_type),
        "image": (filename, io.BytesIO(image_bytes), mime),
    }

    try:
        resp = requests.post(url, headers=headers, files=files, timeout=30)
        resp.raise_for_status()
        data = resp.json()
        code = data.get("code", -1)
        if code != 0:
            logger.error(f"上传图片失败: code={code}, msg={data.get('msg')}")
            return None
        image_key = data.get("data", {}).get("image_key", "")
        logger.info(f"图片上传成功: image_key={image_key[:30]}... (格式: {mime})")
        return image_key
    except requests.exceptions.HTTPError as e:
        logger.error(f"上传图片 HTTP 错误: {e}")
        return None
    except Exception as e:
        logger.error(f"上传图片异常: {e}")
        return None


def send_feishu_image(client: lark.Client, open_id: str, image_key: str) -> bool:
    """
    发送图片消息到飞书单聊。

    API: POST https://open.feishu.cn/open-apis/im/v1/messages?receive_id_type=open_id
    Body: {"receive_id": "xxx", "msg_type": "image", "content": "{\"image_key\":\"xxx\"}"}
    """
    request = (
        CreateMessageRequest.builder()
        .receive_id_type("open_id")
        .request_body(
            CreateMessageRequestBody.builder()
            .receive_id(open_id)
            .msg_type("image")
            .content(json.dumps({"image_key": image_key}, ensure_ascii=False))
            .build()
        )
        .build()
    )

    try:
        response = client.im.v1.message.create(request)
        if not response.success():
            logger.error(f"发送图片消息失败: code={response.code}, msg={response.msg}")
            return False
        logger.info(f"图片消息已发送: {open_id[:12]}...")
        return True
    except Exception as e:
        logger.error(f"发送图片消息异常: {e}")
        return False


def send_feishu_images_batch(
    client: lark.Client,
    open_id: str,
    image_data_list: list,
    header_text: str = "",
) -> int:
    """
    批量上传并发送多张图片。
    先发一条文本说明（如有），然后逐一发送图片。

    Args:
        client: 飞书 SDK 客户端
        open_id: 接收者 open_id
        image_data_list: [(image_bytes, caption_text), ...] 每个元素是 (图片数据, 说明文字)
        header_text: 最前面的总说明文字

    Returns: 成功发送的图片数量
    """
    sent_count = 0

    # 发送总说明
    if header_text:
        send_feishu_text(client, open_id, header_text)

    for i, item in enumerate(image_data_list):
        if isinstance(item, tuple) and len(item) == 2:
            img_bytes, caption = item
        else:
            img_bytes = item if isinstance(item, bytes) else item[0]
            caption = ""

        # 上传图片
        image_key = upload_feishu_image(img_bytes)
        if not image_key:
            logger.error(f"第 {i+1} 张图片上传失败，跳过")
            continue

        # 发送图片
        if send_feishu_image(client, open_id, image_key):
            sent_count += 1
            if caption:
                send_feishu_text(client, open_id, caption)

    return sent_count


def create_client(app_id: str, app_secret: str) -> lark.Client:
    """创建飞书 SDK 客户端（自动管理 token）"""
    return (
        lark.Client.builder()
        .app_id(app_id)
        .app_secret(app_secret)
        .log_level(lark.LogLevel.WARNING)
        .build()
    )


# ==================== Bitable 操作 ====================

def bitable_list_records(
    client: lark.Client,
    app_token: str,
    table_id: str,
    filter_str: str = None,
) -> list[AppTableRecord]:
    """从 Bitable 读取记录（自动翻页）"""
    all_records = []
    page_token = None

    while True:
        req_builder = (
            ListAppTableRecordRequest.builder()
            .app_token(app_token)
            .table_id(table_id)
            .page_size(200)
        )
        if filter_str:
            req_builder = req_builder.filter(filter_str)
        if page_token:
            req_builder = req_builder.page_token(page_token)

        request = req_builder.build()
        response = client.bitable.v1.app_table_record.list(request)

        if not response.success():
            logger.error(f"读取 Bitable 失败: {response.msg}, code={response.code}")
            break

        items = response.data.items or []
        all_records.extend(items)

        if not response.data.has_more:
            break
        page_token = response.data.page_token

    return all_records


def bitable_add_record(
    client: lark.Client,
    app_token: str,
    table_id: str,
    fields: dict,
) -> Optional[str]:
    """添加单条记录到 Bitable"""
    request = (
        CreateAppTableRecordRequest.builder()
        .app_token(app_token)
        .table_id(table_id)
        .request_body(
            AppTableRecord.builder()
            .fields(fields)
            .build()
        )
        .build()
    )

    response = client.bitable.v1.app_table_record.create(request)

    if not response.success():
        logger.error(f"添加 Bitable 记录失败: {response.msg}, code={response.code}")
        return None

    return response.data.record.record_id


# ==================== 飞书消息发送 ====================

def send_feishu_card(
    client: lark.Client,
    open_id: str,
    title: str,
    content: str,
) -> bool:
    """发送飞书卡片消息"""
    card = {
        "config": {"wide_screen_mode": True},
        "header": {
            "title": {"tag": "plain_text", "content": title},
            "template": "blue",
        },
        "elements": [
            {"tag": "markdown", "content": content}
        ],
    }

    request = (
        CreateMessageRequest.builder()
        .receive_id_type("open_id")
        .request_body(
            CreateMessageRequestBody.builder()
            .receive_id(open_id)
            .msg_type("interactive")
            .content(json.dumps(card, ensure_ascii=False))
            .build()
        )
        .build()
    )

    response = client.im.v1.message.create(request)
    return response.success()


def send_feishu_text(
    client: lark.Client,
    open_id: str,
    text: str,
) -> bool:
    """发送飞书文本消息（备用）"""
    request = (
        CreateMessageRequest.builder()
        .receive_id_type("open_id")
        .request_body(
            CreateMessageRequestBody.builder()
            .receive_id(open_id)
            .msg_type("text")
            .content(json.dumps({"text": text}, ensure_ascii=False))
            .build()
        )
        .build()
    )

    response = client.im.v1.message.create(request)
    return response.success()
