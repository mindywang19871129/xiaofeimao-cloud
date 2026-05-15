"""
飞书 API 封装层
================
使用 lark-oapi SDK 封装飞书接口调用。
SDK 自动管理 tenant_access_token，无需手动获取和缓存。
"""

import json
import logging
from typing import Optional

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
