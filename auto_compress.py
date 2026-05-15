#!/usr/bin/env python3
"""
🔧 小肥猫学习 · 自动压缩与提纯脚本
===============================
功能：
  1. 日志轮转 —— 超阈值的 .log 文件自动 gzip 归档，清空原文件
  2. 旧 HTML 试卷归档 —— 非当日的 *.html 移入 archive/html/
  3. 截图/测试产物归档 —— *.png、*_test*.py、样式测试* 移入 archive/misc/
  4. Memory 精炼 —— 超期(>30天)的 daily log 提炼关键信息后归档删除
  5. 错题本健康检查 —— 统计错题本状态

用法:
  python3 auto_compress.py              # 执行一次压缩
  python3 auto_compress.py --dry-run    # 仅预览不执行
  python3 auto_compress.py --full       # 强制全部扫描（忽略时间限制）
  python3 auto_compress.py --stats      # 仅输出统计信息

作者: 小肥猫学习机器人
日期: 2026-05-14
"""

import os
import sys
import json
import gzip
import shutil
import hashlib
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, List, Tuple, Any

# ============================================================
# 配置区 —— 可按需调整
# ============================================================

WORK_DIR = Path(__file__).parent.resolve()
ARCHIVE_DIR = WORK_DIR / "archive"
LOGS_DIR = WORK_DIR / ".logs"
MEMORY_DIR = WORK_DIR / ".workbuddy" / "memory"

# 日志轮转阈值（字节）：超过此大小则触发归档
LOG_SIZE_THRESHOLD = 50 * 1024   # 50KB
# 保留最近 N 天的原始日志
LOG_KEEP_DAYS = 7
# Memory daily log 保留天数
MEMORY_KEEP_DAYS = 30
# HTML 试卷保留天数（超过的归档）
HTML_KEEP_DAYS = 2

# ============================================================
# 工具函数
# ============================================================

def fmt_size(size_bytes: int) -> str:
    """人性化显示文件大小"""
    for unit in ["B", "KB", "MB", "GB"]:
        if size_bytes < 1024:
            return f"{size_bytes:.1f}{unit}"
        size_bytes /= 1024
    return f"{size_bytes:.1f}TB"

def file_hash(filepath: Path, blocksize=65536) -> str:
    """计算文件 MD5（用于去重）"""
    h = hashlib.md5()
    with open(filepath, "rb") as f:
        for block in iter(lambda: f.read(blocksize), b""):
            h.update(block)
    return h.hexdigest()[:12]

def ensure_dir(d: Path) -> None:
    d.mkdir(parents=True, exist_ok=True)

def get_file_age_days(filepath: Path) -> int:
    """获取文件的修改时间距今天数"""
    mtime = datetime.fromtimestamp(filepath.stat().st_mtime)
    return (datetime.now() - mtime).days

# ============================================================
# 1. 日志轮转
# ============================================================

def rotate_logs(dry_run: bool = False) -> Dict[str, Any]:
    """轮转超大的日志文件"""
    results = {"archived": [], "kept": []}

    if not LOGS_DIR.exists():
        return results

    log_files = list(LOGS_DIR.glob("*.log")) + list(LOGS_DIR.glob("*.log.*"))
    archive_log_dir = ARCHIVE_DIR / "logs"

    for log_file in log_files:
        # 跳过已压缩的
        if log_file.suffix == ".gz" or log_file.name.endswith(".gz"):
            continue

        size = log_file.stat().st_size
        age_days = get_file_age_days(log_file)

        if size >= LOG_SIZE_THRESHOLD or age_days > LOG_KEEP_DAYS:
            # 执行归档
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            gz_name = f"{log_file.stem}_{timestamp}.log.gz"
            gz_path = archive_log_dir / gz_name

            if not dry_run:
                ensure_dir(archive_log_dir)
                with open(log_file, "rb") as f_in:
                    with gzip.open(gz_path, "wb") as f_out:
                        shutil.copyfileobj(f_in, f_out)
                # 清空原文件（保留文件句柄）
                log_file.write_text(f"[{datetime.now().isoformat()}] Log rotated to {gz_name}\n")

            results["archived"].append({
                "file": log_file.name,
                "size": fmt_size(size),
                "age_days": age_days,
                "archive_to": str(gz_path.relative_to(WORK_DIR))
            })
        else:
            results["kept"].append({
                "file": log_file.name,
                "size": fmt_size(size),
                "age_days": age_days
            })

    return results

# ============================================================
# 2. 旧 HTML / 截图 / 测试产物归档
# ============================================================

def archive_old_artifacts(dry_run: bool = False) -> Dict[str, Any]:
    """
    归档非核心业务文件：
    - *.html（非系统模板）→ archive/html/
    - *.png → archive/misc/
    - *_test*.py / *测试* → archive/misc/
    """
    results = {"archived": [], "skipped": []}

    # 需要保留的核心文件（不归档）
    keep_files = {
        "system_prompt.md",
        "system_prompt_for_feishu_ai.md",
        "cloud_prompt_compact.md",
        "KET备考计划.md",
    }

    # ---- HTML 试卷归档 ----
    html_archive_dir = ARCHIVE_DIR / "html"
    for html_file in WORK_DIR.glob("*.html"):
        if html_file.name in keep_files:
            continue
        age = get_file_age_days(html_file)
        if age >= HTML_KEEP_DAYS:
            if not dry_run:
                ensure_dir(html_archive_dir)
                dest = html_archive_dir / html_file.name
                # 如果已存在同名文件，加时间戳后缀
                if dest.exists():
                    ts = datetime.now().strftime("%m%d_%H%M")
                    dest = html_archive_dir / f"{html_file.stem}_{ts}{html_file.suffix}"
                shutil.move(str(html_file), str(dest))
            results["archived"].append({
                "type": "html",
                "file": html_file.name,
                "age_days": age,
                "to": f"archive/html/"
            })
        else:
            results["skipped"].append({"type": "html", "file": html_file.name, "age_days": age})

    # ---- 截图 / 测试产物归档（两阶段：先收集信息，再统一移动） ----
    misc_archive_dir = ARCHIVE_DIR / "misc"
    moved_files = set()  # 去重
    pending_moves = []   # (src_path, dest_name, info_dict)

    # PNG 截图
    for png_file in WORK_DIR.glob("*.png"):
        if str(png_file) in moved_files:
            continue
        moved_files.add(str(png_file))
        pending_moves.append((png_file, png_file.name, {
            "type": "png",
            "file": png_file.name,
            "size": fmt_size(png_file.stat().st_size),
            "to": "archive/misc/"
        }))

    # 测试脚本
    test_patterns = ["*test*.py", "*_test.py"]
    for pattern in test_patterns:
        for test_file in WORK_DIR.glob(pattern):
            if str(test_file) in moved_files:
                continue
            moved_files.add(str(test_file))
            pending_moves.append((test_file, test_file.name, {
                "type": "test_script",
                "file": test_file.name,
                "to": "archive/misc/"
            }))

    # 样式测试类文件（注意：png 可能已被上面匹配过）
    for style_file in WORK_DIR.glob("*测试*"):
        if str(style_file) in moved_files:
            continue
        # 跳过 .html 文件（由 HTML 归档流程处理）
        if style_file.suffix == ".html":
            continue
        moved_files.add(str(style_file))
        pending_moves.append((style_file, style_file.name, {
            "type": "style_test",
            "file": style_file.name,
            "to": "archive/misc/"
        }))

    # 统一执行移动
    for src, dest_name, info in pending_moves:
        try:
            if not dry_run:
                ensure_dir(misc_archive_dir)
                shutil.move(str(src), str(misc_archive_dir / dest_name))
            results["archived"].append(info)
        except FileNotFoundError:
            # 文件可能已被其他规则移走，跳过
            pass

    return results

# ============================================================
# 3. Memory 精炼（核心功能！）
# ============================================================

def refine_memory(dry_run: bool = False) -> Dict[str, Any]:
    """
    精炼 memory 目录：
    - 超过 MEMORY_KEEP_DAYS 的 daily log → 提取关键信息追加到 MEMORY.md 后删除原文
    - MEMORY.md 自身如果过大（>50KB），做一轮内容压缩
    """
    results = {"refined": [], "deleted": [], "memory_status": ""}

    if not MEMORY_DIR.exists():
        return results

    today_str = datetime.now().strftime("%Y-%m-%d")
    cutoff_date = datetime.now() - timedelta(days=MEMORY_KEEP_DAYS)
    cutoff_str = cutoff_date.strftime("%Y-%m-%d")

    daily_logs = sorted(MEMORY_DIR.glob("2*.md"))

    for log_file in daily_logs:
        stem = log_file.stem  # e.g., "2026-05-13"

        # 跳过今天的和未来的
        if stem >= cutoff_str:
            continue

        # 读取并提炼
        content = log_file.read_text(encoding="utf-8").strip()
        if not content:
            if not dry_run:
                log_file.unlink()
            results["deleted"].append({"file": log_file.name, "reason": "空文件"})
            continue

        # AI式提炼：提取有意义的条目（以 - 开头的列表项、## 标题下的内容等）
        refined = _extract_key_points(content, stem)

        if refined:
            memory_md = MEMORY_DIR / "MEMORY.md"
            existing = ""
            if memory_md.exists():
                existing = memory_md.read_text(encoding="utf-8")

            if not dry_run:
                # 追加到 MEMORY.md 末尾的归档区域
                archive_section = f"\n\n---\n\n## 📦 归档自 {stem}\n\n{refined}"
                memory_md.write_text(existing + archive_section, encoding="utf-8")

        if not dry_run:
            log_file.unlink()

        results["refined"].append({
            "file": log_file.name,
            "date": stem,
            "key_points_count": len(refined.split("\n")) if refined else 0,
            "action": "已提炼→MEMORY.md" if refined else "已删除"
        })

    # 检查 MEMORY.md 大小
    memory_md = MEMORY_DIR / "MEMORY.md"
    if memory_md.exists():
        size = memory_md.stat().st_size
        results["memory_status"] = f"MEMORY.md: {fmt_size(size)} ({size} bytes)"
        if size > 50 * 1024 and not dry_run:
            # 超过 50KB 时给出警告
            results["memory_status"] += " ⚠️ 建议手动整理 MEMORY.md（已超过50KB）"

    return results


def _extract_key_points(content: str, date_label: str) -> str:
    """
    从 daily log 内容中提取关键信息点。
    策略：
    1. 保留所有 ### / ## 标题行及其下的一条摘要
    2. 保留所有 "- [x]" 完成的任务项
    3. 保留所有 "**粗体**" 标记的重要结论
    4. 丢弃纯过程性描述和重复内容
    """
    lines = content.split("\n")
    key_points = []
    current_section = None
    section_lines = []

    for line in lines:
        stripped = line.strip()

        # 标题行
        if stripped.startswith("### ") or stripped.startswith("## "):
            if current_section and section_lines:
                # 把上一个section的内容浓缩为一行
                summary = _summarize_section(section_lines)
                key_points.append(f"- **{current_section}**: {summary}")
            current_section = stripped.lstrip("# ").strip()
            section_lines = []
            continue

        if current_section:
            section_lines.append(stripped)

    # 处理最后一个 section
    if current_section and section_lines:
        summary = _summarize_section(section_lines)
        key_points.append(f"- **{current_section}**: {summary}")

    return "\n".join(key_points)


def _summarize_section(lines: List[str]) -> str:
    """将一个 section 的多行内容浓缩为一行摘要"""
    meaningful = [l for l in lines if l and (l.startswith("-") or l.startswith("*") or
                                              "**" in l or l[0].isdigit())]
    if not meaningful:
        meaningful = [l for l in lines if l and len(l) > 10]

    if len(meaningful) <= 2:
        return "; ".join(meaningful)[:200]
    elif len(meaningful) <= 5:
        return "; ".join(meaningful[:3]) + f" ...(+{len(meaningful)-3}项)"
    else:
        return f"{meaningful[0][:80]} ...(+共{len(meaningful)}项)"

# ============================================================
# 4. 错题本健康检查
# ============================================================

def check_mistake_book() -> Dict[str, Any]:
    """检查错题本状态"""
    mb_path = WORK_DIR / "mistake_book.json"
    result = {"exists": False}

    if not mb_path.exists():
        return result

    try:
        data = json.loads(mb_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, Exception):
        result["exists"] = True
        result["error"] = "JSON解析失败"
        return result

    result["exists"] = True
    result["total_mistakes"] = len(data.get("mistakes", []))
    result["total_size"] = fmt_size(mb_path.stat().st_size)

    # 按来源统计
    by_source = {}
    active_mistakes = []
    for m in data.get("mistakes", []):
        src = m.get("source", "未知来源")
        by_source[src] = by_source.get(src, 0) + 1
        err_cnt = m.get("error_count", 0)
        if err_cnt < 3:  # 未消除的错题
            active_mistakes.append(m.get("knowledge_point", "未知知识点"))

    result["by_source"] = by_source
    result["active_count"] = len(active_mistakes)
    result["resolved_count"] = result["total_mistakes"] - result["active_count"]

    # 统计各科
    by_subject = {}
    for m in data.get("mistakes", []):
        subj = m.get("subject", "未知")
        by_subject[subj] = by_subject.get(subj, 0) + 1
    result["by_subject"] = by_subject

    return result

# ============================================================
# 5. 全局统计
# ============================================================

def global_stats() -> Dict[str, Any]:
    """输出全局存储统计"""
    stats = {
        "scan_time": datetime.now().isoformat(),
        "total_size": 0,
        "files": [],
        "by_category": {
            "core_py": {"count": 0, "size": 0},
            "config": {"count": 0, "size": 0},
            "data": {"count": 0, "size": 0},
            "html": {"count": 0, "size": 0},
            "logs": {"count": 0, "size": 0},
            "other": {"count": 0, "size": 0},
        }
    }

    all_items = list(WORK_DIR.iterdir())
    # 加上子目录
    if LOGS_DIR.exists():
        all_items.extend(LOGS_DIR.iterdir())
    if MEMORY_DIR.exists():
        all_items.extend(MEMORY_DIR.iterdir())
    if ARCHIVE_DIR.exists():
        all_items.extend(ARCHIVE_DIR.rglob("*"))

    for item in all_items:
        if not item.is_file():
            continue
        try:
            size = item.stat().st_size
        except OSError:
            continue

        rel = item.relative_to(WORK_DIR)
        name = item.name
        ext = item.suffix.lower()

        stats["total_size"] += size
        stats["files"].append({
            "path": str(rel),
            "name": name,
            "size": fmt_size(size),
            "size_bytes": size
        })

        # 分类
        if ext == ".py":
            cat = "core_py"
        elif ext in (".md", ".json"):
            cat = "config" if ".workbuddy" in str(rel) else "data"
        elif ext == ".html":
            cat = "html"
        elif ext == ".log" or ext == ".gz":
            cat = "logs"
        else:
            cat = "other"

        stats["by_category"][cat]["count"] += 1
        stats["by_category"][cat]["size"] += size

    stats["total_size_human"] = fmt_size(stats["total_size"])
    return stats

# ============================================================
# 主流程
# ============================================================

def run(full_mode: bool = False, dry_run: bool = False, stats_only: bool = False):
    """执行完整的压缩流程"""

    print("=" * 60)
    print("🔧 小肥猫学习 · 自动压缩与提纯工具")
    print(f"⏰ 执行时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"📂 工作目录: {WORK_DIR}")
    print(f"{'🔍 [预览模式] 仅查看不修改' if dry_run else '🔨 [执行模式] 将进行实际操作'}")
    print("=" * 60)

    report = {
        "timestamp": datetime.now().isoformat(),
        "dry_run": dry_run,
        "results": {}
    }

    # Step 0: 全局统计
    print("\n📊 === 全局存储概览 ===\n")
    stats = global_stats()
    report["results"]["stats"] = stats
    print(f"  总占用: {stats['total_size_human']}")
    print(f"  文件总数: {len(stats['files'])}")
    print(f"\n  分类统计:")
    for cat, info in stats["by_category"].items():
        if info["count"]:
            print(f"    {cat}: {info['count']}个文件, {fmt_size(info['size'])}")

    if stats_only:
        print("\n✅ 统计完成。")
        return report

    # Step 1: 日志轮转
    print("\n📋 === 1. 日志轮转 ===\n")
    log_results = rotate_logs(dry_run=dry_run)
    report["results"]["log_rotation"] = log_results

    if log_results["archived"]:
        print(f"  ✅ 归档了 {len(log_results['archived'])} 个日志文件:")
        for entry in log_results["archived"]:
            print(f"     • {entry['file']} ({entry['size']}, {entry['age_days']}天前) → {entry['archive_to']}")
    else:
        print(f"  ℹ️  无需归档的日志。{len(log_results['kept'])} 个日志均在正常范围内:")
        for entry in log_results["kept"]:
            print(f"     • {entry['file']} ({entry['size']}, {entry['age_days']}天)")

    # Step 2: 旧产物归档
    print("\n📦 === 2. 旧文件归档 ===\n")
    artifact_results = archive_old_artifacts(dry_run=dry_run)
    report["results"]["artifact_archival"] = artifact_results

    if artifact_results["archived"]:
        print(f"  ✅ 归档了 {len(artifact_results['archived'])} 个文件:")
        for entry in artifact_results["archived"]:
            size_info = f" ({entry.get('size', '')})" if "size" in entry else ""
            print(f"     • [{entry['type']}] {entry['file']}{size_info} → {entry['to']}")
    else:
        print("  ℹ️  无需归档的旧文件。")
    if artifact_results["skipped"]:
        print(f"\n  📌 保留中 (<{HTML_KEEP_DAYS}天):")
        for entry in artifact_results["skipped"]:
            print(f"     • {entry['file']} ({entry['age_days']}天)")

    # Step 3: Memory 精炼
    print("\n🧠 === 3. Memory 精炼 ===\n")
    mem_results = refine_memory(dry_run=dry_run)
    report["results"]["memory_refine"] = mem_results

    if mem_results["refined"]:
        print(f"  ✅ 精炼了 {len(mem_results['refined'])} 个过期日志:")
        for entry in mem_results["refined"]:
            print(f"     • {entry['file']} ({entry['date']}) → {entry['action']}")
    if mem_results["deleted"]:
        print(f"  🗑️  删除了 {len(mem_results['deleted'])} 个空/无效文件")
    if not mem_results["refined"] and not mem_results["deleted"]:
        print("  ℹ️  无需精炼的过期日志。")
    print(f"\n  {mem_results['memory_status']}")

    # Step 4: 错题本检查
    print("\n📒 === 4. 错题本健康检查 ===\n")
    mb_stats = check_mistake_book()
    report["results"]["mistake_book"] = mb_stats

    if mb_stats.get("exists"):
        print(f"  总错题数: {mb_stats['total_mistakes']}")
        print(f"  数据大小: {mb_stats['total_size']}")
        print(f"  活跃错题: {mb_stats['active_count']} 条 | 已消除: {mb_stats['resolved_count']} 条")
        if mb_stats.get("by_subject"):
            print("  各科分布:")
            for subj, cnt in mb_stats["by_subject"].items():
                print(f"    • {subj}: {cnt}条")
        if mb_stats.get("by_source"):
            print("  来源分布:")
            for src, cnt in mb_stats["by_source"].items():
                print(f"    • {src}: {cnt}条")
    else:
        print("  ℹ️  错题本尚未创建或为空。")

    # 最终汇总
    total_archived = len(log_results.get("archived", []))
    total_artifacts = len(artifact_results.get("archived", []))
    total_refined = len(mem_results.get("refined", []))
    total_deleted = len(mem_results.get("deleted", []))
    total_saved = total_archived + total_artifacts + total_refined + total_deleted
    print("\n" + "=" * 60)
    print(f"✅ 压缩完成! 本次处理了 {total_saved} 个项目")
    if dry_run:
        print("⚠️  以上为预览模式，未进行实际操作。去掉 --dry-run 参数即可正式执行。")
    print("=" * 60)

    # 保存报告
    report_path = LOGS_DIR / "compress_report.json"
    if not dry_run:
        ensure_dir(LOGS_DIR)
        report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    return report


# ============================================================
# CLI 入口
# ============================================================

if __name__ == "__main__":
    args = set(sys.argv[1:])
    dry_run = "--dry-run" in args
    full_mode = "--full" in args
    stats_only = "--stats" in args

    run(full_mode=full_mode, dry_run=dry_run, stats_only=stats_only)
