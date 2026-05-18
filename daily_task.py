#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
小肥猫学习 - 每日任务调度脚本 v2.3
被 launchd 定时调用（每天 09:00），执行完整流程：
  0. 从 Bitable 同步云函数批改的错题到本地
  1. 调用 question_generator.py 生成当日新题
  2. 周五特殊模式: 生成周五+周六+周日三合一
  3. 调用 feishu_push.py 将题目推送到飞书
  4. 同步题目到飞书多维表格（供云函数批改时读取）
     周五模式: 三天题目逐个日期推送到 Bitable

用法：
  python3 daily_task.py              # 执行完整流程
  python3 daily_task.py --dry-run     # 只生成不推送（测试用）
  python3 daily_task.py --force       # 强制重新生成（覆盖当天已有题目）
"""

import json
import os
import sys
import logging
from pathlib import Path
from datetime import datetime, timedelta

# ==================== 配置区 ====================

WORK_DIR = Path(__file__).parent.resolve()
DAILY_QUESTIONS_FILE = WORK_DIR / "daily_questions.json"
WEEKEND_BUNDLE_FILE = WORK_DIR / "weekend_bundle.json"
LOG_DIR = WORK_DIR / ".logs"
LOG_FILE = LOG_DIR / "daily_task.log"

# ==================== 日志初始化 ====================
LOG_DIR.mkdir(exist_ok=True)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler(LOG_FILE, encoding="utf-8"),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger("daily_task")

WEEKDAY_NAMES = ["周一", "周二", "周三", "周四", "周五", "周六", "周日"]


# ==================== 工具函数 ====================

def is_friday(dt=None):
    """判断是否是周五"""
    dt = dt or datetime.now()
    return dt.weekday() == 4  # Monday=0, Friday=4


def get_friday_bundle_dates(dt=None):
    """
    获取周五三合一包的目标日期
    Returns: [(日期字符串, 标签), ...]  e.g. [("2026-05-15","周五"), ("2026-05-16","周六"), ("2026-05-17","周日")]
    """
    dt = dt or datetime.now()
    dates = []
    for i in range(3):
        target = dt + timedelta(days=i)
        dates.append((
            target.strftime("%Y-%m-%d"),
            WEEKDAY_NAMES[target.weekday()]
        ))
    return dates


# ==================== 步骤1：生成新题 ====================

def step_generate(force=False, target_date_str=None):
    """
    调用 question_generator 生成新题目
    Args:
        force: 是否强制重新生成（忽略已存在的题目）
        target_date_str: 指定目标日期（用于周五批处理），默认今天
    Returns:
        (success, data_or_error_msg)
    """
    today = target_date_str or datetime.now().strftime("%Y-%m-%d")

    # 非 force 模式下检查是否已有
    if not force and target_date_str is None and DAILY_QUESTIONS_FILE.exists():
        try:
            existing = json.loads(DAILY_QUESTIONS_FILE.read_text(encoding="utf-8"))
            if existing.get("date") == today and existing.get("status") != "graded":
                logger.info(f"⏭️ 今天({today})的题目已经存在，状态={existing.get('status')}，跳过生成")
                return True, existing
        except Exception:
            pass

    sys.path.insert(0, str(WORK_DIR))
    try:
        import question_generator
        success, result = question_generator.generate_daily_questions(
            target_date_str=target_date_str
        )
        if success:
            return True, result
        else:
            return False, f"出题失败: {result}"
    except Exception as e:
        logger.error(f"调用出题模块异常: {e}", exc_info=True)
        return False, f"出题模块异常: {e}"


def step_generate_weekend_bundle(force=False):
    """
    周五特殊模式：批量生成周五+周六+周日三天的题目
    Returns:
        (success, {date_label: data_dict, ...} or error_msg)
    """
    logger.info("🎯 周五特殊模式 — 生成三天合一题目包")
    bundle = {}
    dates = get_friday_bundle_dates()

    for date_str, label in dates:
        logger.info(f"  正在生成 {label} ({date_str}) 的题目...")
        try:
            success, data = step_generate(force=force, target_date_str=date_str)
            if not success:
                return False, f"{label}({date_str})生成失败: {data}"
            bundle[f"{date_str}|{label}"] = {
                "date": date_str,
                "label": label,
                "data": data
            }
            logger.info(f"  ✅ {label} 题目生成成功")
        except Exception as e:
            return False, f"{label}({date_str})生成异常: {e}"

    # 保存三合一套餐数据
    bundle_data = {
        "generated_at": datetime.now().isoformat(),
        "days": bundle
    }
    WEEKEND_BUNDLE_FILE.write_text(
        json.dumps(bundle_data, ensure_ascii=False, indent=2),
        encoding="utf-8"
    )
    logger.info(f"💾 三天套餐已保存到 {WEEKEND_BUNDLE_FILE}")
    return True, bundle


# ==================== 步骤2：构建推送内容 ====================

def build_push_content(questions_data):
    """
    从 daily_questions.json 数据构建飞书卡片消息内容
    Returns:
        (title, card_content) 卡片标题和 Markdown 正文
    """
    date_str = questions_data.get("date", datetime.now().strftime("%Y-%m-%d"))
    total_score = questions_data.get("total_score", 0)

    title = f"📚 每日学习卷 · {date_str}"

    content = f"**📅 日期：{date_str}**\n\n"

    # 数学部分
    if "math" in questions_data:
        math_data = questions_data["math"]
        topic = math_data.get("topic", "数学练习")
        content += f"### 📐 **数学 · {topic}** （共{math_data.get('total_score', 0)}分 | {math_data.get('count', 0)}道题）\n\n"
        for i, q in enumerate(math_data.get("questions", []), 1):
            content += f"**第{i}题【{q['type']}】（{q['score']}分）**\n"
            content += f"{q['content']}\n\n"

    content += "---\n\n"

    # 英语部分
    if "english" in questions_data:
        eng_data = questions_data["english"]
        grammar = eng_data.get("grammar_topic", "KET练习")
        content += f"### 📘 **英语 KET · {grammar}** （共{eng_data.get('total_score', 0)}分 | {eng_data.get('count', 0)}道题）\n\n"

        new_words = eng_data.get("new_words", [])
        if new_words:
            content += "**🆕 今日必背单词：**\n"
            if isinstance(new_words, list) and len(new_words) > 0 and isinstance(new_words[0], dict):
                word_list = " | ".join([f"{w.get('word', w)}" for w in new_words])
            else:
                word_list = " | ".join(new_words) if isinstance(new_words, list) else str(new_words)
            content += f"{word_list}\n\n"

        for i, q in enumerate(eng_data.get("questions", []), 1):
            content += f"**第{i}题【{q['type']}】（{q['score']}分）**\n"
            content += f"{q['content']}\n\n"

    content += "---\n\n"
    content += "> ✅ 做完后将答案回复给我，我来批改解析！\n"
    content += "> 💡 答案格式示例：`M1=83 M2=44 E1=forget/arrive/plan`\n"
    content += f"> 📊 本卷总分: **{total_score}分**"

    return title, content


def build_weekend_bundle_content(bundle):
    """
    从周末三天数据构建合并推送内容
    Returns:
        (title, card_content)
    """
    today_str = datetime.now().strftime("%Y-%m-%d")
    title = f"📚 周末学习套餐 · 周五+周六+周日 · {today_str}"

    content = f"**🐱 小肥猫学习 · 周末三合一学习券**\n\n"
    content += f"> 📅 生成日期: {today_str}\n"
    content += f"> 🗓️ 覆盖: 周五 / 周六 / 周日\n"
    content += f"> 💡 三天题目一次性下达，自由安排每天做一份！\n\n"
    content += "---\n\n"

    total_all_score = 0
    total_all_questions = 0

    for key, day_info in bundle.items():
        date_str = day_info["date"]
        label = day_info["label"]
        data = day_info["data"]

        math_data = data.get("math", {})
        eng_data = data.get("english", {})
        math_score = math_data.get("total_score", 0)
        eng_score = eng_data.get("total_score", 0)
        math_count = math_data.get("count", 0)
        eng_count = eng_data.get("count", 0)
        day_total = math_score + eng_score
        day_count = math_count + eng_count
        total_all_score += day_total
        total_all_questions += day_count

        content += f"## 📌 {label} ({date_str})\n\n"
        content += f"| 科目 | 内容 | 题量 | 分数 |\n"
        content += f"|------|------|------|------|\n"
        content += f"| 📐 数学 | {math_data.get('topic', '数学练习')} | {math_count}题 | {math_score}分 |\n"
        content += f"| 📘 英语 | {eng_data.get('grammar_topic', 'KET练习')} | {eng_count}题 | {eng_score}分 |\n"
        content += f"| **小计** | | **{day_count}题** | **{day_total}分** |\n\n"

        # 列出数学题目概要
        if math_data.get("questions"):
            content += f"**📐 数学题目列表：**\n"
            for i, q in enumerate(math_data.get("questions", []), 1):
                content += f"**第{i}题【{q['type']}】（{q['score']}分）**\n{q['content']}\n\n"

        # 列出英语题目概要
        if eng_data.get("questions"):
            content += f"**📘 英语题目列表：**\n"
            new_words = eng_data.get("new_words", [])
            if new_words:
                if isinstance(new_words, list) and len(new_words) > 0 and isinstance(new_words[0], dict):
                    word_list = " | ".join([f"{w.get('word', w)}" for w in new_words])
                else:
                    word_list = " | ".join(new_words) if isinstance(new_words, list) else str(new_words)
                content += f"🆕 新词: {word_list}\n\n"
            for i, q in enumerate(eng_data.get("questions", []), 1):
                content += f"**第{i}题【{q['type']}】（{q['score']}分）**\n{q['content']}\n\n"

        content += "---\n\n"

    # 三天汇总
    content += f"## 📊 三天总计\n\n"
    content += f"| 指标 | 数值 |\n"
    content += f"|------|------|\n"
    content += f"| 总题量 | **{total_all_questions} 题** |\n"
    content += f"| 总分数 | **{total_all_score} 分** |\n"
    content += f"| 建议安排 | 每天一份，周末做完 |\n\n"

    content += "---\n\n"
    content += "> ✅ 做完后将答案回复给我，每天分别提交我来批改！\n"
    content += "> 💡 答案格式：`日期 M1=83 M2=44 E1=forget/arrive/plan`\n"
    content += "> 📒 错题会自动加入错题本，后续定期复习"

    return title, content


# ==================== 步骤3：推送到飞书 ====================

def step_push(title, content, dry_run=False):
    """
    调用 feishu_push 模块发送卡片消息到飞书
    Args:
        dry_run: 如果为True，只打印不实际发送
    Returns:
        bool 是否成功
    """
    if dry_run:
        logger.info("🔇 Dry-run 模式 — 不实际推送消息")
        print(f"\n{'='*60}")
        print(f"【预览 - 推送内容】")
        print(f"{'='*60}")
        print(f"标题: {title}")
        print(f"\n{content}")
        print(f"{'='*60}")
        return True

    # 导入推送模块
    sys.path.insert(0, str(WORK_DIR))
    import feishu_push

    try:
        success = feishu_push.push_daily(
            custom_content=content,
            date_str=title.replace("📚 每日学习卷 · ", "")
        )
        return success
    except Exception as e:
        logger.error(f"推送失败: {e}", exc_info=True)
        return False


# ==================== 步骤0：开机同步（出题前从 Bitable 拉取云函数批改的错题） ====================

def step_sync_mistakes_from_bitable():
    """
    从 Bitable 同步云函数批改产生的错题到本地 mistake_book.json
    Returns:
        (synced_count, skipped_count) 同步和跳过的数量
    """
    try:
        sys.path.insert(0, str(WORK_DIR))
        import bitable_sync
        result = bitable_sync.sync_mistakes()
        synced = result.get("synced", 0)
        skipped = result.get("skipped", 0)
        if synced > 0 or skipped > 0:
            logger.info(f"🔄 Bitable 错题同步: 新增{synced}条 / 跳过{skipped}条")
        else:
            logger.info("📭 无新错题需要同步")
        return synced, skipped
    except Exception as e:
        logger.error(f"错题同步异常: {e}", exc_info=True)
        return 0, 0


# ==================== 步骤4：同步到飞书多维表格 ====================

def step_push_to_bitable(questions_data):
    """
    将当日题目同步到飞书多维表格（供云函数批改时读取）
    Returns:
        bool 是否成功
    """
    if questions_data is None:
        logger.info("⏭️ 无题目数据，跳过 Bitable 同步")
        return False

    try:
        sys.path.insert(0, str(WORK_DIR))
        import feishu_bitable

        config = feishu_bitable.load_bitable_config()
        if not config:
            logger.warning("⚠️ Bitable 未初始化，跳过同步。运行 python3 feishu_bitable.py --init 初始化")
            return False

        date_str = questions_data.get("date", datetime.now().strftime("%Y-%m-%d"))
        record_ids = feishu_bitable.push_daily_questions_to_bitable(
            config["app_token"],
            config["daily_table_id"],
            date_str,
            questions_data
        )
        logger.info(f"✅ Bitable 同步成功: {len(record_ids)} 条题目记录")
        return True

    except Exception as e:
        logger.error(f"Bitable 同步失败: {e}", exc_info=True)
        return False


def step_push_weekend_to_bitable(bundle):
    """
    周五模式：将三天题目全部推送到 Bitable（供云函数批改时逐日读取）
    Args:
        bundle: {"2026-05-15|周五": {"date":..., "label":..., "data":...}, ...}
    Returns:
        (pushed_days, total_records) 成功推送的天数和总记录数
    """
    if not bundle:
        logger.info("⏭️ 周末套餐为空，跳过 Bitable 同步")
        return 0, 0

    try:
        sys.path.insert(0, str(WORK_DIR))
        import feishu_bitable

        config = feishu_bitable.load_bitable_config()
        if not config:
            logger.warning("⚠️ Bitable 未初始化，跳过周末同步")
            return 0, 0

        app_token = config["app_token"]
        table_id = config["daily_table_id"]
        pushed_days = 0
        total_records = 0

        for key, day_info in bundle.items():
            date_str = day_info["date"]
            label = day_info["label"]
            data = day_info["data"]

            record_ids = feishu_bitable.push_daily_questions_to_bitable(
                app_token, table_id, date_str, data
            )
            logger.info(f"  ✅ {label}({date_str}): {len(record_ids)} 条题目 → Bitable")
            pushed_days += 1
            total_records += len(record_ids)

        logger.info(f"✅ 周末Bitable同步完成: {pushed_days}天/{total_records}条记录")
        return pushed_days, total_records

    except Exception as e:
        logger.error(f"周末Bitable同步失败: {e}", exc_info=True)
        return 0, 0


# ==================== 主流程 ====================

def run(dry_run=False, force=False):
    """执行完整的每日任务流程"""
    logger.info("=" * 50)
    logger.info("🐱 小肥猫学习 — 每日任务调度 开始")
    logger.info("=" * 50)

    start_time = datetime.now()
    friday_mode = is_friday()

    # ===== 步骤0: 从 Bitable 同步错题（拉取云函数批改结果）=====
    logger.info("\n🔄 [0/4] 从 Bitable 同步错题...")
    step_sync_mistakes_from_bitable()

    # ===== 周五特殊模式 =====
    if friday_mode:
        logger.info("\n🎯 检测到周五 — 启动三天合一模式")
        logger.info("\n📝 [1/4] 批量生成三天题目...")
        success, bundle = step_generate_weekend_bundle(force=force)

        if not success:
            logger.error(f"❌ 周末套餐生成失败: {bundle}")
            send_error_notification(f"周末套餐出题失败: {bundle}")
            return False

        logger.info("\n📋 [2/4] 构建三天套餐推送消息...")
        title, content = build_weekend_bundle_content(bundle)
    else:
        # ===== 正常每日模式 =====
        logger.info("\n📝 [1/4] 生成今日题目...")
        success, data = step_generate(force=force)

        if not success:
            logger.error(f"❌ 题目生成失败: {data}")
            send_error_notification(f"每日出题失败: {data}")
            return False

        logger.info("\n📋 [2/4] 构建推送消息...")
        title, content = build_push_content(data)

    # Step 3: 推送到飞书（周五和普通模式共用）
    logger.info("\n📤 [3/4] 推送到飞书...")
    push_success = step_push(title, content, dry_run=dry_run)

    # Step 4: 同步题目到飞书多维表格（云函数批改需要）
    if friday_mode:
        logger.info("\n📊 [4/4] 同步三天套餐到飞书多维表格...")
        pushed_days, total_records = step_push_weekend_to_bitable(bundle)
        bitable_success = pushed_days > 0
    else:
        logger.info("\n📊 [4/4] 同步题目到飞书多维表格...")
        bitable_success = step_push_to_bitable(data)

    elapsed = (datetime.now() - start_time).total_seconds()
    status = "✅ 成功" if push_success else ("⏭️ Dry-run" if dry_run else "❌ 失败")
    bt_status = "✅" if bitable_success else "⚠️"
    bt_detail = f"({pushed_days}天/{total_records}条)" if friday_mode and bitable_success else ""
    mode_tag = " [周五三天套餐]" if friday_mode else ""

    logger.info(f"\n{'='*50}")
    logger.info(f"🐱 每日任务调度完成 [{status}]{mode_tag} | Bitable:{bt_status} | 耗时: {elapsed:.1f}s")
    logger.info(f"{'='*50}")

    return push_success


def send_error_notification(error_msg: str):
    """出错时尝试发一条错误通知给家长"""
    try:
        sys.path.insert(0, str(WORK_DIR))
        import feishu_push
        token = feishu_push.get_token()
        feishu_push.send_text_message(
            token,
            feishu_push.RECEIVE_ID,
            feishu_push.RECEIVE_TYPE,
            f"⚠️ 小肥猫学习自动出题遇到问题:\n{error_msg}\n\n请检查服务器日志或手动触发。"
        )
    except Exception as e:
        logger.error(f"发送错误通知也失败了: {e}")


# ==================== CLI 入口 ====================

if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="小肥猫学习 - 每日任务调度")
    parser.add_argument("--dry-run", action="store_true", help="只生成和预览，不实际推送")
    parser.add_argument("--force", action="store_true", help="强制重新生成（覆盖已有题目）")

    args = parser.parse_args()

    success = run(dry_run=args.dry_run, force=args.force)
    sys.exit(0 if success else 1)
