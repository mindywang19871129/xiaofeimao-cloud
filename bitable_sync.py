#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
小肥猫学习 - Bitable → 本地错题同步模块 v1.0
=============================================
功能：从飞书多维表格同步云函数批改产生的错题到本地 mistake_book.json

使用场景：
- Mac 关机期间，Vercel 云函数批改的错题存在飞书 Bitable 中
- Mac 开机后，此模块将未同步错题拉取到本地，并入错题本复习循环

触发方式：
1. Mac 开机自动运行（launchd 配置）
2. 定时运行（如每30分钟）
3. 手动运行: python3 bitable_sync.py
"""

import json
import sys
import logging
from pathlib import Path
from datetime import datetime

import feishu_bitable

# ==================== 配置区 ====================

WORK_DIR = Path(__file__).parent.resolve()
MISTAKE_BOOK_FILE = WORK_DIR / "mistake_book.json"

LOG_DIR = WORK_DIR / ".logs"
LOG_FILE = LOG_DIR / "bitable_sync.log"

LOG_DIR.mkdir(exist_ok=True)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler(LOG_FILE, encoding="utf-8"),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger("bitable_sync")


# ==================== 本地错题本操作 ====================

def load_local_mistakes() -> list:
    """加载本地错题本"""
    try:
        if MISTAKE_BOOK_FILE.exists():
            text = MISTAKE_BOOK_FILE.read_text(encoding="utf-8")
            if text.strip():
                data = json.loads(text)
                return data.get("mistakes", [])
    except Exception as e:
        logger.error(f"读取本地错题本失败: {e}")
    return []


def save_local_mistakes(mistakes: list):
    """保存本地错题本"""
    data = {
        "updated_at": datetime.now().isoformat(),
        "total_mistakes": len(mistakes),
        "mistakes": mistakes
    }
    MISTAKE_BOOK_FILE.write_text(
        json.dumps(data, ensure_ascii=False, indent=2),
        encoding="utf-8"
    )
    logger.info(f"💾 本地错题本已保存: {len(mistakes)} 条")


def _is_duplicate(local_mistakes: list, new_mistake: dict) -> bool:
    """
    判断是否为重复错题（同一知识点+同一题目内容）
    如果重复，更新 error_count 而不是新增
    """
    for m in local_mistakes:
        # 相同科目 + 相同知识点 + 相似题目内容
        if (m.get("subject") == new_mistake.get("subject") and
                m.get("knowledge_point") == new_mistake.get("knowledge_point") and
                m.get("question_content") == new_mistake.get("question_content")):
            # 更新错误次数
            m["error_count"] = m.get("error_count", 1) + 1
            m["status"] = "new"  # 重置为需要复习
            m["source"] = f"{m.get('source','')},Bitable同步"
            return True
    return False


# ==================== 同步逻辑 ====================

def sync_mistakes() -> dict:
    """
    主同步流程
    Returns: {
        "synced": int,      # 成功同步数
        "skipped": int,     # 跳过数（重复）
        "total_in_bitable": int,  # 云函数总共产生的错题数
        "errors": list      # 错误列表
    }
    """
    logger.info("🔄 开始 Bitable → 本地错题同步...")

    # 1. 加载 Bitable 配置
    config = feishu_bitable.load_bitable_config()
    if not config:
        msg = "⚠️ Bitable 未初始化，跳过同步。请先运行 python3 feishu_bitable.py --init"
        logger.warning(msg)
        return {"synced": 0, "skipped": 0, "total_in_bitable": 0, "errors": [msg]}

    app_token = config.get("app_token")
    mistake_table_id = config.get("mistake_table_id")

    if not app_token or not mistake_table_id:
        msg = "⚠️ Bitable 配置不完整"
        logger.warning(msg)
        return {"synced": 0, "skipped": 0, "total_in_bitable": 0, "errors": [msg]}

    # 2. 获取未同步的错题
    logger.info("📥 查询未同步错题...")
    try:
        unsynced_records = feishu_bitable.get_unsynced_mistakes(app_token, mistake_table_id)
    except Exception as e:
        msg = f"查询Bitable失败: {e}"
        logger.error(msg)
        return {"synced": 0, "skipped": 0, "total_in_bitable": 0, "errors": [msg]}

    if not unsynced_records:
        logger.info("📭 没有需要同步的错题")
        return {"synced": 0, "skipped": 0, "total_in_bitable": 0, "errors": []}

    # 3. 统计云函数总共产生的错题
    all_mistakes = feishu_bitable.list_all_records(app_token, mistake_table_id)
    cloud_total = len([r for r in all_mistakes if r.get("fields", {}).get("来源") == "云函数批改"])

    # 4. 加载本地错题本
    local_mistakes = load_local_mistakes()
    logger.info(f"📋 本地错题本现有 {len(local_mistakes)} 条")

    # 5. 合并同步
    synced_count = 0
    skipped_count = 0
    synced_ids = []
    errors = []

    for record in unsynced_records:
        try:
            # 转换为本地格式
            new_mistake = feishu_bitable.bitable_record_to_mistake(record)

            # 去重检查
            if _is_duplicate(local_mistakes, new_mistake):
                skipped_count += 1
                synced_ids.append(record["record_id"])
                continue

            # 添加复习时间
            today = datetime.now().strftime("%Y-%m-%d")
            new_mistake["last_review_date"] = None
            new_mistake["next_review_date"] = _get_next_review_date(1, today)

            # 添加进本地错题本
            local_mistakes.append(new_mistake)
            synced_count += 1
            synced_ids.append(record["record_id"])

        except Exception as e:
            err_msg = f"同步记录失败: {record.get('record_id','?')} - {e}"
            logger.error(err_msg)
            errors.append(err_msg)

    # 6. 保存本地错题本
    if synced_count > 0 or skipped_count > 0:
        save_local_mistakes(local_mistakes)

    # 7. 标记 Bitable 中的记录为已同步
    if synced_ids:
        try:
            feishu_bitable.mark_mistakes_synced(app_token, mistake_table_id, synced_ids)
        except Exception as e:
            logger.error(f"标记已同步失败: {e}")

    result = {
        "synced": synced_count,
        "skipped": skipped_count,
        "total_in_bitable": cloud_total,
        "errors": errors
    }

    logger.info(f"✅ 同步完成: 新增{synced_count}条 | 跳过{skipped_count}条 | 错误{len(errors)}条")
    return result


def _get_next_review_date(error_count: int, base_date: str) -> str:
    """根据艾宾浩斯间隔计算下次复习日期"""
    from datetime import timedelta

    # 间隔天数的4个阶段
    intervals = [1, 3, 7, 14]
    # 根据错误次数选择间隔（循环）
    idx = min((error_count - 1) % len(intervals) if error_count > 0 else 0, 3)
    days = intervals[idx]

    try:
        dt = datetime.strptime(base_date, "%Y-%m-%d")
        next_dt = dt + timedelta(days=days)
        return next_dt.strftime("%Y-%m-%d")
    except ValueError:
        return datetime.now().strftime("%Y-%m-%d")


# ==================== 同步状态汇报 ====================

def get_sync_status() -> dict:
    """获取同步状态概览"""
    config = feishu_bitable.load_bitable_config()

    result = {
        "bitable_configured": bool(config),
        "bitable_url": config.get("url", "") if config else "",
        "local_mistakes": 0,
        "cloud_total": 0,
        "unsynced": 0,
    }

    # 本地错题统计
    local = load_local_mistakes()
    result["local_mistakes"] = len(local)

    if not config:
        return result

    # 云端统计
    try:
        app_token = config["app_token"]
        table_id = config["mistake_table_id"]

        all_records = feishu_bitable.list_all_records(app_token, table_id)
        result["cloud_total"] = len(all_records)

        unsynced = [r for r in all_records if not r.get("fields", {}).get("是否已同步", False)]
        result["unsynced"] = len(unsynced)
    except Exception as e:
        result["error"] = str(e)

    return result


# ==================== CLI 入口 ====================

if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="小肥猫学习 - Bitable 错题同步")
    parser.add_argument("--status", action="store_true", help="显示同步状态")
    parser.add_argument("--dry-run", action="store_true", help="默认是同步模式")

    args = parser.parse_args()

    if args.status:
        status = get_sync_status()
        print("\n📊 Bitable 同步状态")
        print(f"   {'─' * 40}")
        print(f"   多维表格配置: {'✅ 已配置' if status['bitable_configured'] else '❌ 未配置'}")
        if status.get("bitable_url"):
            print(f"   多维表格链接: {status['bitable_url']}")
        print(f"   本地错题数: {status['local_mistakes']} 条")
        print(f"   云端总错题: {status.get('cloud_total', 0)} 条")
        print(f"   待同步: {status.get('unsynced', 0)} 条")
        if status.get("error"):
            print(f"   ⚠️ 错误: {status['error']}")
        print()

    else:
        # 执行同步
        result = sync_mistakes()

        print(f"\n📊 同步结果")
        print(f"   {'─' * 40}")
        print(f"   新增: {result['synced']} 条")
        print(f"   跳过(重复): {result['skipped']} 条")
        print(f"   云端总数: {result['total_in_bitable']} 条")
        if result.get("errors"):
            print(f"   错误: {len(result['errors'])} 条")
            for e in result["errors"][:5]:
                print(f"     - {e[:100]}")
        print()

        sys.exit(0 if not result["errors"] else 1)
