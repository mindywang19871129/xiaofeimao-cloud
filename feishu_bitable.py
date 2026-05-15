#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
小肥猫学习 - 飞书多维表格 (Bitable) 模块 v1.0
==============================================
功能：
1. 创建多维表格应用（2张表：每日题目 + 错题本）
2. 每日题目表：存储已推送的题目和答案（云端题目仓库）
3. 错题本表：存储批改发现的错题（云端错题仓库）
4. 提供 CRUD 操作封装

架构定位：
- Mac 开机时：daily_task.py 生成题目 → 写入 Bitable 每日题目表 → 推送飞书卡片
- Mac 关机时：Vercel 云函数从 Bitable 读题 → 调用 DeepSeek 批改 → 错题写入 Bitable 错题本表
- Mac 再次开机：bitable_sync.py 从 Bitable 错题本表同步到本地 mistake_book.json
"""

import json
import os
import sys
import time
import logging
from pathlib import Path
from datetime import datetime
from typing import Optional

import requests

# ==================== 配置区 ====================

WORK_DIR = Path(__file__).parent.resolve()
BITABLE_CONFIG_FILE = WORK_DIR / "bitable_config.json"

APP_ID = "cli_aa8f8d25a925dbea"
APP_SECRET = "9vyD11qA4jIxn3PCQB1jnfvTXMXs2Rve"

FEISHU_API_BASE = "https://open.feishu.cn/open-apis"

# 日志
LOG_DIR = WORK_DIR / ".logs"
LOG_FILE = LOG_DIR / "bitable.log"

LOG_DIR.mkdir(exist_ok=True)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler(LOG_FILE, encoding="utf-8"),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger("feishu_bitable")


# ==================== 表结构定义 ====================

# 表1：每日题目
DAILY_QUESTIONS_TABLE = {
    "name": "每日题目",
    "fields": [
        {"field_name": "日期", "type": 1},                # 文本
        {"field_name": "科目", "type": 3, "property": {   # 单选
            "options": [
                {"name": "数学", "color": 1},
                {"name": "英语", "color": 2},
            ]
        }},
        {"field_name": "题号", "type": 1},                # 文本 (M1/M2/E1等)
        {"field_name": "题型", "type": 1},                # 文本
        {"field_name": "题目内容", "type": 1},            # 文本（多行）
        {"field_name": "正确答案", "type": 1},            # 文本（多行）
        {"field_name": "详细解析", "type": 1},            # 文本（多行）
        {"field_name": "分值", "type": 2},                # 数字
        {"field_name": "知识点", "type": 1},              # 文本
        {"field_name": "家长答案格式提示", "type": 1},    # 文本
        {"field_name": "推送时间", "type": 5, "property": {  # 日期
            "date_formatter": "yyyy-MM-dd HH:mm",
            "auto_fill": False
        }},
    ]
}

# 表2：错题本
MISTAKE_BOOK_TABLE = {
    "name": "错题本",
    "fields": [
        {"field_name": "日期", "type": 1},                # 文本
        {"field_name": "科目", "type": 3, "property": {   # 单选
            "options": [
                {"name": "数学", "color": 1},
                {"name": "英语", "color": 2},
            ]
        }},
        {"field_name": "题号", "type": 1},                # 文本
        {"field_name": "题型", "type": 1},                # 文本
        {"field_name": "题目内容", "type": 1},            # 文本
        {"field_name": "孩子答案", "type": 1},            # 文本
        {"field_name": "正确答案", "type": 1},            # 文本
        {"field_name": "错因分析", "type": 1},            # 文本（多行）
        {"field_name": "知识点", "type": 1},              # 文本
        {"field_name": "错误次数", "type": 2},            # 数字
        {"field_name": "状态", "type": 3, "property": {   # 单选
            "options": [
                {"name": "新错题", "color": 1},
                {"name": "复习中", "color": 2},
                {"name": "已掌握", "color": 3},
            ]
        }},
        {"field_name": "来源", "type": 3, "property": {   # 单选
            "options": [
                {"name": "Mac本地批改", "color": 1},
                {"name": "云函数批改", "color": 2},
                {"name": "手动录入", "color": 3},
            ]
        }},
        {"field_name": "是否已同步", "type": 7},          # 复选框
        {"field_name": "录入时间", "type": 5, "property": {
            "date_formatter": "yyyy-MM-dd HH:mm",
            "auto_fill": False
        }},
    ]
}


# ==================== Token 管理 ====================

_token_cache = {"token": "", "expires_at": 0}


def get_token() -> str:
    """获取 tenant_access_token（带内存缓存，避免频繁请求）"""
    now = time.time()
    # 提前60秒刷新，避免边界过期
    if _token_cache["token"] and _token_cache["expires_at"] > now + 60:
        return _token_cache["token"]

    url = f"{FEISHU_API_BASE}/auth/v3/tenant_access_token/internal"
    try:
        resp = requests.post(url, json={
            "app_id": APP_ID,
            "app_secret": APP_SECRET
        }, timeout=15)
        data = resp.json()
    except requests.exceptions.Timeout:
        raise Exception("获取飞书Token超时，请检查网络")
    except requests.exceptions.RequestException as e:
        raise Exception(f"获取飞书Token网络错误: {e}")

    if data.get("code") != 0:
        raise Exception(f"获取Token失败: {data.get('msg', data)}")

    token = data["tenant_access_token"]
    expires_in = data.get("expire", 1800)
    _token_cache["token"] = token
    _token_cache["expires_at"] = now + expires_in
    return token


# ==================== 配置持久化 ====================

def load_bitable_config() -> dict:
    """加载 Bitable 配置"""
    if BITABLE_CONFIG_FILE.exists():
        return json.loads(BITABLE_CONFIG_FILE.read_text(encoding="utf-8"))
    return {}


def save_bitable_config(config: dict):
    """保存 Bitable 配置"""
    BITABLE_CONFIG_FILE.write_text(
        json.dumps(config, ensure_ascii=False, indent=2),
        encoding="utf-8"
    )
    logger.info(f"💾 Bitable配置已保存: app_token={config.get('app_token','?')}")


# ==================== Bitable 应用创建 ====================

def create_bitable_app(name: str = "小肥猫学习·题目与错题", folder_token: str = None) -> dict:
    """
    创建多维表格应用
    Returns: {"app_token": "xxx", "default_table_id": "xxx", "url": "xxx"}
    """
    token = get_token()
    url = f"{FEISHU_API_BASE}/bitable/v1/apps"
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json; charset=utf-8"
    }

    body = {"name": name}
    if folder_token:
        body["folder_token"] = folder_token

    resp = requests.post(url, headers=headers, json=body, timeout=15)
    data = resp.json()

    if data.get("code") != 0:
        raise Exception(f"创建多维表格失败: {data.get('msg', data)}")

    app = data["data"]["app"]
    logger.info(f"✅ 创建多维表格成功: {app['name']}")
    logger.info(f"   app_token: {app['app_token']}")
    logger.info(f"   url: {app['url']}")
    return app


# ==================== 数据表创建 ====================

def create_table(app_token: str, table_def: dict) -> str:
    """
    在指定应用中创建数据表
    Args:
        app_token: 应用标识
        table_def: 表定义 {"name": "...", "fields": [...]}
    Returns: table_id
    """
    token = get_token()
    url = f"{FEISHU_API_BASE}/bitable/v1/apps/{app_token}/tables"
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json; charset=utf-8"
    }

    body = {
        "table": {
            "name": table_def["name"],
            "fields": table_def["fields"]
        }
    }

    # 飞书 API 创建表时可以同时定义字段
    # 但实际测试中部分字段类型可能需要分步创建
    # 先尝试一次性创建
    resp = requests.post(url, headers=headers, json=body, timeout=20)
    data = resp.json()

    if data.get("code") != 0:
        logger.warning(f"创建表 {table_def['name']} 失败: {data.get('msg', data)}")
        # 尝试降级：先创建空表，再逐个添加字段
        return _create_table_fallback(app_token, table_def)

    table_id = data["data"]["table_id"]
    logger.info(f"✅ 创建数据表成功: {table_def['name']} (id={table_id})")
    return table_id


def _create_table_fallback(app_token: str, table_def: dict) -> str:
    """降级方案：先创建空表，再逐个添加字段"""
    token = get_token()

    # Step 1: 创建空表
    url = f"{FEISHU_API_BASE}/bitable/v1/apps/{app_token}/tables"
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json; charset=utf-8"
    }

    body = {"table": {"name": table_def["name"]}}
    resp = requests.post(url, headers=headers, json=body, timeout=15)
    data = resp.json()

    if data.get("code") != 0:
        raise Exception(f"创建空表失败: {data.get('msg', data)}")

    table_id = data["data"]["table_id"]
    logger.info(f"✅ 创建空表成功: {table_def['name']} (id={table_id})")

    # Step 2: 批量添加字段
    # 飞书支持批量创建字段: POST .../tables/{table_id}/fields/batch_create
    batch_url = f"{FEISHU_API_BASE}/bitable/v1/apps/{app_token}/tables/{table_id}/fields/batch_create"
    batch_body = {"fields": table_def["fields"]}

    resp2 = requests.post(batch_url, headers=headers, json=batch_body, timeout=20)
    data2 = resp2.json()

    if data2.get("code") != 0:
        logger.error(f"批量添加字段失败: {data2.get('msg', data2)}")
        # 最后尝试：逐个添加
        return _create_fields_one_by_one(app_token, table_id, table_def["fields"])

    logger.info(f"✅ 批量添加 {len(table_def['fields'])} 个字段成功")
    return table_id


def _create_fields_one_by_one(app_token: str, table_id: str, fields: list) -> str:
    """逐个添加字段（最慢但最可靠的降级方案）"""
    token = get_token()
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json; charset=utf-8"
    }

    for i, field in enumerate(fields):
        url = f"{FEISHU_API_BASE}/bitable/v1/apps/{app_token}/tables/{table_id}/fields"
        resp = requests.post(url, headers=headers, json=field, timeout=10)
        data = resp.json()
        if data.get("code") != 0:
            logger.warning(f"  字段 {field['field_name']} 添加失败: {data.get('msg', '?')}")
        else:
            logger.info(f"  [{i+1}/{len(fields)}] ✅ {field['field_name']}")
        time.sleep(0.3)  # 避免限频

    return table_id


# ==================== 记录 CRUD ====================

def add_records(app_token: str, table_id: str, records: list) -> list:
    """
    批量添加记录
    Args:
        records: [{"fields": {"列名": 值, ...}}, ...]
    Returns: 创建的 record_ids 列表
    """
    if not records:
        return []

    token = get_token()
    url = f"{FEISHU_API_BASE}/bitable/v1/apps/{app_token}/tables/{table_id}/records/batch_create"
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json; charset=utf-8"
    }

    # 分批：每次最多500条
    batch_size = 500
    all_ids = []

    for i in range(0, len(records), batch_size):
        batch = records[i:i+batch_size]
        body = {"records": batch}
        resp = requests.post(url, headers=headers, json=body, timeout=30)
        data = resp.json()

        if data.get("code") != 0:
            logger.error(f"批量添加记录失败: {data.get('msg', data)}")
            # 尝试逐条添加
            for rec in batch:
                try:
                    rid = add_single_record(app_token, table_id, rec["fields"])
                    if rid:
                        all_ids.append(rid)
                except Exception as e:
                    logger.error(f"单条添加失败: {e}")
        else:
            batch_ids = [r["record_id"] for r in data["data"].get("records", [])]
            all_ids.extend(batch_ids)
            logger.info(f"✅ 批量添加 {len(batch)} 条记录成功")

    return all_ids


def add_single_record(app_token: str, table_id: str, fields: dict) -> Optional[str]:
    """添加单条记录，返回 record_id"""
    token = get_token()
    url = f"{FEISHU_API_BASE}/bitable/v1/apps/{app_token}/tables/{table_id}/records"
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json; charset=utf-8"
    }

    body = {"fields": fields}
    resp = requests.post(url, headers=headers, json=body, timeout=15)
    data = resp.json()

    if data.get("code") != 0:
        logger.error(f"添加记录失败: {data.get('msg', data)}")
        return None

    return data["data"]["record"]["record_id"]


def list_records(app_token: str, table_id: str, filter_str: str = None,
                 page_size: int = 100, page_token: str = None) -> dict:
    """
    列出记录
    Args:
        filter_str: 筛选条件，如 'CurrentValue.[日期] = "2026-05-15"'
    Returns: {"records": [...], "has_more": bool, "page_token": str}
    """
    token = get_token()
    url = f"{FEISHU_API_BASE}/bitable/v1/apps/{app_token}/tables/{table_id}/records"
    headers = {"Authorization": f"Bearer {token}"}

    params = {"page_size": min(page_size, 500)}
    if filter_str:
        params["filter"] = filter_str
    if page_token:
        params["page_token"] = page_token

    try:
        resp = requests.get(url, headers=headers, params=params, timeout=20)
        resp.raise_for_status()
        data = resp.json()
    except requests.exceptions.Timeout:
        logger.error(f"查询Bitable超时: table={table_id}")
        return {"records": [], "has_more": False, "page_token": None}
    except requests.exceptions.ConnectionError as e:
        logger.error(f"查询Bitable连接失败: {e}")
        return {"records": [], "has_more": False, "page_token": None}
    except requests.exceptions.HTTPError as e:
        logger.error(f"查询Bitable HTTP错误: {e} (status={resp.status_code if 'resp' in dir() else '?'})")
        return {"records": [], "has_more": False, "page_token": None}
    except requests.exceptions.RequestException as e:
        logger.error(f"查询Bitable网络错误: {e}")
        return {"records": [], "has_more": False, "page_token": None}
    except (json.JSONDecodeError, ValueError) as e:
        logger.error(f"查询Bitable返回非JSON响应: {e}")
        return {"records": [], "has_more": False, "page_token": None}
    except Exception as e:
        logger.error(f"查询Bitable未知错误: {e}", exc_info=True)
        return {"records": [], "has_more": False, "page_token": None}

    if data.get("code") != 0:
        logger.error(f"查询记录失败: code={data.get('code')}, msg={data.get('msg','?')}")
        return {"records": [], "has_more": False, "page_token": None}

    result = data.get("data")
    if result is None:
        logger.warning(f"查询Bitable返回空data: table={table_id}")
        return {"records": [], "has_more": False, "page_token": None}

    return {
        "records": (result.get("items") or []) if isinstance(result, dict) else [],
        "has_more": result.get("has_more", False) if isinstance(result, dict) else False,
        "page_token": result.get("page_token", None) if isinstance(result, dict) else None
    }


def list_all_records(app_token: str, table_id: str, filter_str: str = None) -> list:
    """列出所有记录（自动翻页）"""
    all_records = []
    page_token = None

    while True:
        result = list_records(app_token, table_id, filter_str=filter_str,
                              page_token=page_token)
        all_records.extend(result["records"])
        if not result["has_more"]:
            break
        page_token = result["page_token"]

    return all_records


def update_record(app_token: str, table_id: str, record_id: str, fields: dict) -> bool:
    """更新单条记录的字段"""
    token = get_token()
    url = f"{FEISHU_API_BASE}/bitable/v1/apps/{app_token}/tables/{table_id}/records/{record_id}"
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json; charset=utf-8"
    }

    body = {"fields": fields}
    resp = requests.put(url, headers=headers, json=body, timeout=15)
    data = resp.json()

    if data.get("code") != 0:
        logger.error(f"更新记录失败: {data.get('msg', data)}")
        return False
    return True


# ==================== 高级操作 ====================

def push_daily_questions_to_bitable(app_token: str, table_id: str,
                                     date_str: str, questions_data: dict) -> list:
    """
    将每日题目推送到 Bitable
    Args:
        questions_data: daily_questions.json 的数据结构
    Returns: record_ids 列表
    """
    records = []
    now_str = datetime.now().strftime("%Y-%m-%d %H:%M")

    # 数学题
    for q in questions_data.get("math", {}).get("questions", []):
        records.append({
            "fields": {
                "日期": date_str,
                "科目": "数学",
                "题号": q["id"],
                "题型": q["type"],
                "题目内容": q["content"],
                "正确答案": str(q["correct_answer"]),
                "详细解析": q.get("explanation", ""),
                "分值": q.get("score", 0),
                "知识点": q.get("knowledge_point", ""),
                "家长答案格式提示": q.get("answer_format", ""),
                "推送时间": _date_to_timestamp(now_str),
            }
        })

    # 英语题
    for q in questions_data.get("english", {}).get("questions", []):
        records.append({
            "fields": {
                "日期": date_str,
                "科目": "英语",
                "题号": q["id"],
                "题型": q["type"],
                "题目内容": q["content"],
                "正确答案": str(q["correct_answer"]),
                "详细解析": q.get("explanation", ""),
                "分值": q.get("score", 0),
                "知识点": q.get("knowledge_point", ""),
                "家长答案格式提示": q.get("answer_format", ""),
                "推送时间": _date_to_timestamp(now_str),
            }
        })

    logger.info(f"📤 推送 {len(records)} 道题目到 Bitable 每日题目表...")
    return add_records(app_token, table_id, records)


def add_mistake_to_bitable(app_token: str, table_id: str, mistake: dict,
                            source: str = "云函数批改") -> Optional[str]:
    """
    添加一条错题到 Bitable 错题本表
    Args:
        mistake: 错题数据字典
        source: 来源标签
    """
    now_str = datetime.now().strftime("%Y-%m-%d %H:%M")

    fields = {
        "日期": mistake.get("date", datetime.now().strftime("%Y-%m-%d")),
        "科目": mistake.get("subject", ""),
        "题号": mistake.get("question_id", ""),
        "题型": mistake.get("question_type", ""),
        "题目内容": mistake.get("question_content", ""),
        "孩子答案": str(mistake.get("student_answer", "")),
        "正确答案": str(mistake.get("correct_answer", "")),
        "错因分析": mistake.get("error_reason", "")[:2000],  # 飞书限制
        "知识点": mistake.get("knowledge_point", ""),
        "错误次数": mistake.get("error_count", 1),
        "状态": "新错题",
        "来源": source,
        "是否已同步": False,
        "录入时间": _date_to_timestamp(now_str),
    }

    return add_single_record(app_token, table_id, fields)


def get_unsynced_mistakes(app_token: str, table_id: str, max_retries: int = 2) -> list:
    """
    获取尚未同步到本地的错题（是否已同步 != true）
    
    注意：不用 API filter 筛 checkbox 字段（飞书 Bitable 对 checkbox null/false 
    的筛选行为不稳定，可能返回空结果或 NoneType）。改为拉取全量再 Python 侧筛选。
    
    Returns: 未同步的错题记录列表
    """
    for attempt in range(max_retries):
        try:
            # 拉取全量记录（不加 filter，避免 checkbox 筛选异常）
            all_records = list_all_records(app_token, table_id)
            
            # Python 侧筛选：未同步 = 字段不存在 OR 值为 False/None
            unsynced = []
            for rec in all_records:
                fields = rec.get("fields", {}) or {}
                is_synced = fields.get("是否已同步")
                # checkbox 字段：False / None / 不存在 → 视为未同步
                if not is_synced:
                    unsynced.append(rec)
            
            logger.info(f"📥 找到 {len(unsynced)} 条未同步错题（共 {len(all_records)} 条）")
            return unsynced
            
        except Exception as e:
            if attempt < max_retries - 1:
                wait = (attempt + 1) * 3
                logger.warning(f"查询未同步错题失败 (尝试 {attempt+1}/{max_retries}): {e}，{wait}秒后重试...")
                time.sleep(wait)
            else:
                logger.error(f"查询未同步错题最终失败: {e}")
                raise


def mark_mistakes_synced(app_token: str, table_id: str, record_ids: list):
    """将指定错题标记为已同步"""
    for rid in record_ids:
        update_record(app_token, table_id, rid, {"是否已同步": True})
    logger.info(f"✅ 标记 {len(record_ids)} 条错题为已同步")


# ==================== 初始化（一键创建） ====================

def init_bitable(force_recreate: bool = False) -> dict:
    """
    一键初始化 Bitable 环境
    1. 如果已有配置且不强制重建 → 直接返回
    2. 否则创建应用 + 两张表
    Returns: {"app_token": "...", "daily_table_id": "...", "mistake_table_id": "...", "url": "..."}
    """
    existing = load_bitable_config()

    if existing and not force_recreate:
        logger.info(f"📋 Bitable 已配置: app_token={existing.get('app_token','?')}")
        return existing

    logger.info("🚀 开始初始化飞书多维表格...")
    token = get_token()

    # Step 1: 创建应用
    logger.info("[1/3] 创建多维表格应用...")
    try:
        app = create_bitable_app("小肥猫学习·题目与错题")
    except Exception as e:
        logger.error(f"创建应用失败: {e}")
        raise

    app_token = app["app_token"]
    default_table_id = app.get("default_table_id", "")

    # Step 2: 删除默认空表（如果有），然后创建两张业务表
    logger.info("[2/3] 创建数据表...")

    # 先建表：每日题目（第一张自定义表）
    daily_table_id = create_table(app_token, DAILY_QUESTIONS_TABLE)

    # 再建表：错题本
    mistake_table_id = create_table(app_token, MISTAKE_BOOK_TABLE)

    # 如果有默认空表，可以删除它
    if default_table_id and default_table_id not in [daily_table_id, mistake_table_id]:
        try:
            _delete_table(app_token, default_table_id)
        except Exception:
            pass  # 删不掉就算了，不阻塞流程

    # Step 3: 保存配置
    logger.info("[3/3] 保存配置...")
    config = {
        "app_token": app_token,
        "daily_table_id": daily_table_id,
        "mistake_table_id": mistake_table_id,
        "url": app["url"],
        "created_at": datetime.now().isoformat(),
    }
    save_bitable_config(config)

    logger.info(f"🎉 多维表格初始化完成！")
    logger.info(f"   📊 应用URL: {app['url']}")
    logger.info(f"   📝 每日题目表: {daily_table_id}")
    logger.info(f"   📒 错题本表: {mistake_table_id}")
    return config


def _delete_table(app_token: str, table_id: str):
    """删除数据表"""
    token = get_token()
    url = f"{FEISHU_API_BASE}/bitable/v1/apps/{app_token}/tables/{table_id}"
    headers = {"Authorization": f"Bearer {token}"}
    resp = requests.delete(url, headers=headers, timeout=10)
    data = resp.json()
    if data.get("code") == 0:
        logger.info(f"🗑️ 删除默认空表成功: {table_id}")


# ==================== 工具函数 ====================

def _date_to_timestamp(date_str: str, fmt: str = "%Y-%m-%d %H:%M") -> int:
    """将日期字符串转为毫秒时间戳（飞书日期字段格式）"""
    try:
        dt = datetime.strptime(date_str, fmt)
        return int(dt.timestamp() * 1000)
    except ValueError:
        try:
            dt = datetime.strptime(date_str, "%Y-%m-%d")
            return int(dt.timestamp() * 1000)
        except ValueError:
            return int(datetime.now().timestamp() * 1000)


def bitable_record_to_mistake(record: dict) -> dict:
    """
    将 Bitable 记录转换为本地 mistake_book.json 格式
    """
    fields = record.get("fields", {})

    def get_text(key, default=""):
        val = fields.get(key, default)
        if isinstance(val, list) and val:
            return str(val[0]) if len(val) == 1 else str(val)
        return str(val) if val else default

    def get_bool(key, default=False):
        return fields.get(key, default) is True

    def get_number(key, default=0):
        val = fields.get(key, default)
        return int(val) if val else default

    return {
        "date": get_text("日期"),
        "subject": get_text("科目"),
        "question_id": get_text("题号"),
        "question_type": get_text("题型"),
        "question_content": get_text("题目内容"),
        "student_answer": get_text("孩子答案"),
        "correct_answer": get_text("正确答案"),
        "error_reason": get_text("错因分析"),
        "knowledge_point": get_text("知识点"),
        "error_count": get_number("错误次数", 1),
        "status": _status_map(get_text("状态", "新错题")),
        "source": get_text("来源", "云函数批改"),
        "bitable_record_id": record.get("record_id", ""),
    }


def _status_map(bitable_status: str) -> str:
    """Bitable 状态 → 本地状态"""
    mapping = {"新错题": "new", "复习中": "reviewing", "已掌握": "mastered"}
    return mapping.get(bitable_status, "new")


# ==================== CLI 入口 ====================

if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="小肥猫学习 - 飞书多维表格管理")
    parser.add_argument("--init", action="store_true", help="一键初始化 Bitable")
    parser.add_argument("--force", action="store_true", help="强制重建")
    parser.add_argument("--push", type=str, default=None, help="推送 daily_questions.json 到 Bitable（传入日期）")
    parser.add_argument("--list-unsynced", action="store_true", help="列出未同步错题")
    parser.add_argument("--mark-synced", action="store_true", help="标记所有错题为已同步")

    args = parser.parse_args()

    if args.init:
        config = init_bitable(force_recreate=args.force)
        print(f"\n✅ Bitable 初始化完成！")
        print(f"   应用链接: {config['url']}")
        print(f"   app_token: {config['app_token']}")
        print(f"   每日题目表ID: {config['daily_table_id']}")
        print(f"   错题本表ID: {config['mistake_table_id']}")

    elif args.push:
        config = load_bitable_config()
        if not config:
            print("❌ 请先运行 --init 初始化 Bitable")
            sys.exit(1)

        # 读取本地 daily_questions.json
        daily_file = WORK_DIR / "daily_questions.json"
        if not daily_file.exists():
            print(f"❌ 文件不存在: {daily_file}")
            sys.exit(1)

        questions_data = json.loads(daily_file.read_text(encoding="utf-8"))
        date_str = args.push or questions_data.get("date", datetime.now().strftime("%Y-%m-%d"))

        ids = push_daily_questions_to_bitable(
            config["app_token"],
            config["daily_table_id"],
            date_str,
            questions_data
        )
        print(f"\n✅ 推送完成！共 {len(ids)} 条记录")

    elif args.list_unsynced:
        config = load_bitable_config()
        if not config:
            print("❌ 请先运行 --init")
            sys.exit(1)

        records = get_unsynced_mistakes(config["app_token"], config["mistake_table_id"])
        print(f"\n📥 未同步错题: {len(records)} 条")
        for r in records:
            f = r.get("fields", {})
            print(f"  - [{f.get('科目','?')}] {f.get('题号','?')} {f.get('知识点','?')} | 孩子答:{f.get('孩子答案','?')} | 正确:{f.get('正确答案','?')}")

    elif args.mark_synced:
        config = load_bitable_config()
        if not config:
            print("❌ 请先运行 --init")
            sys.exit(1)

        records = get_unsynced_mistakes(config["app_token"], config["mistake_table_id"])
        ids = [r["record_id"] for r in records]
        if ids:
            mark_mistakes_synced(config["app_token"], config["mistake_table_id"], ids)
            print(f"\n✅ 已标记 {len(ids)} 条错题为已同步")
        else:
            print("\n📭 没有需要同步的错题")
