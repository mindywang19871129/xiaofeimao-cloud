#!/bin/bash
# ============================================================
#  小肥猫学习 · Mac 本地停服 + 清理脚本
# ============================================================
#  用法:
#    bash stop_and_clean.sh            # 停服 + 备份（安全模式）
#    bash stop_and_clean.sh --purge    # 停服 + 备份 + 删除项目目录
#
#  功能:
#    1. 停止所有 launchd 服务
#    2. 卸载并删除 plist 配置
#    3. 备份日志等重要数据
#    4. 可选：清理项目目录
#
#  注意: 执行前请确认 JumpServer 已正常运行所有服务
# ============================================================

set -e

RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; BLUE='\033[0;34m'; NC='\033[0m'
log_info()  { echo -e "${BLUE}[INFO]${NC}  $*"; }
log_ok()    { echo -e "${GREEN}[OK]${NC}    $*"; }
log_warn()  { echo -e "${YELLOW}[WARN]${NC}  $*"; }
log_err()   { echo -e "${RED}[ERROR]${NC} $*"; }

PURGE_MODE=false
if [ "$1" = "--purge" ] || [ "$1" = "-p" ]; then
    PURGE_MODE=true
fi

PROJECT_DIR="/Users/mindy/WorkBuddy/2026-05-13-task-1"
BACKUP_DIR="${HOME}/xiaofeimao_backup_$(date +%Y%m%d_%H%M%S)"
LAUNCH_AGENTS_DIR="${HOME}/Library/LaunchAgents"

echo ""
echo "=========================================================="
echo "  小肥猫学习 · Mac 本地停服 + 清理"
echo "=========================================================="
echo ""
log_info "模式: $([ "$PURGE_MODE" = true ] && echo '停服+备份+清理' || echo '停服+备份（安全模式）')"
echo ""

# ==================== Step 0: 安全检查 ====================
log_info "[0/4] 安全检查..."

# 检查是否有 sudo（卸载 launchd 不需要，但做个提醒）
if [ "$EUID" -eq 0 ]; then
    log_warn "检测到 root 权限，不需要用 sudo 运行本脚本"
fi

# 列出全部待处理的 plist
PLIST_FILES=(
    "com.xiaofeimao.auto-compress.plist"
    "com.xiaofeimao.bitable-sync.plist"
    "com.xiaofeimao.daily-learning.plist"
    "com.xiaofeimao.bot-server.plist"
)

SERVICE_LABELS=(
    "com.xiaofeimao.auto-compress"
    "com.xiaofeimao.bitable-sync"
    "com.xiaofeimao.daily-learning"
    "com.xiaofeimao.bot-server"
)

FOUND_COUNT=0
for plist in "${PLIST_FILES[@]}"; do
    if [ -f "${LAUNCH_AGENTS_DIR}/${plist}" ]; then
        FOUND_COUNT=$((FOUND_COUNT + 1))
    fi
done

if [ "$FOUND_COUNT" -eq 0 ]; then
    log_warn "未找到任何小肥猫相关 plist，可能已经清理过了"
fi

log_ok "安全检查完成，找到 ${FOUND_COUNT} 个 plist 文件"

# ==================== Step 1: 停止 launchd 服务 ====================
log_info "[1/4] 停止所有 launchd 服务..."

STOPPED_COUNT=0
for label in "${SERVICE_LABELS[@]}"; do
    if launchctl list | grep -q "$label"; then
        log_info "停止: ${label}..."
        launchctl stop "$label" 2>/dev/null || true
        launchctl unload "${LAUNCH_AGENTS_DIR}/${label}.plist" 2>/dev/null || true
        STOPPED_COUNT=$((STOPPED_COUNT + 1))
        log_ok "已停止并卸载: ${label}"
    else
        log_info "${label} 未在运行，跳过"
    fi
done

if [ "$STOPPED_COUNT" -eq 0 ]; then
    log_info "没有运行中的服务需要停止"
else
    log_ok "已停止 ${STOPPED_COUNT} 个服务"
fi

# ==================== Step 2: 删除 plist 文件 ====================
log_info "[2/4] 删除 plist 配置文件..."

REMOVED_COUNT=0
for plist in "${PLIST_FILES[@]}"; do
    PLIST_PATH="${LAUNCH_AGENTS_DIR}/${plist}"
    if [ -f "$PLIST_PATH" ]; then
        log_info "删除: ${plist}"
        rm -f "$PLIST_PATH"
        REMOVED_COUNT=$((REMOVED_COUNT + 1))
        log_ok "已删除: ${plist}"
    fi
done

if [ "$REMOVED_COUNT" -eq 0 ]; then
    log_info "没有需要删除的 plist 文件"
else
    log_ok "已删除 ${REMOVED_COUNT} 个 plist 文件"
fi

# ==================== Step 3: 备份重要数据 ====================
log_info "[3/4] 备份重要数据..."

if [ -d "$PROJECT_DIR" ]; then
    mkdir -p "$BACKUP_DIR"

    # 备份日志
    if [ -d "${PROJECT_DIR}/.logs" ]; then
        log_info "备份日志目录..."
        cp -r "${PROJECT_DIR}/.logs" "${BACKUP_DIR}/logs"
        log_ok "日志已备份到 ${BACKUP_DIR}/logs"
    fi

    # 备份错题本
    if [ -f "${PROJECT_DIR}/mistake_book.json" ]; then
        log_info "备份错题本..."
        cp "${PROJECT_DIR}/mistake_book.json" "${BACKUP_DIR}/mistake_book.json"
        log_ok "错题本已备份"
    fi

    # 备份每日题目缓存
    if [ -f "${PROJECT_DIR}/daily_questions.json" ]; then
        log_info "备份每日题目缓存..."
        cp "${PROJECT_DIR}/daily_questions.json" "${BACKUP_DIR}/daily_questions.json"
        log_ok "每日题目缓存已备份"
    fi

    if [ -f "${PROJECT_DIR}/weekend_bundle.json" ]; then
        cp "${PROJECT_DIR}/weekend_bundle.json" "${BACKUP_DIR}/weekend_bundle.json"
        log_ok "周末题目包已备份"
    fi

    # 备份 .env（如果存在）
    if [ -f "${PROJECT_DIR}/.env" ]; then
        log_info "备份环境变量..."
        cp "${PROJECT_DIR}/.env" "${BACKUP_DIR}/.env"
        chmod 600 "${BACKUP_DIR}/.env"
        log_ok "环境变量已备份"
    fi

    # 备份 workbuddy memory
    if [ -d "${PROJECT_DIR}/.workbuddy" ]; then
        log_info "备份 WorkBuddy 记忆..."
        cp -r "${PROJECT_DIR}/.workbuddy" "${BACKUP_DIR}/workbuddy_memory"
        log_ok "WorkBuddy 记忆已备份"
    fi

    log_ok "数据备份完成 → ${BACKUP_DIR}"
else
    log_warn "项目目录不存在，跳过备份"
fi

# ==================== Step 4: 可选清理 ====================
if [ "$PURGE_MODE" = true ] && [ -d "$PROJECT_DIR" ]; then
    log_info "[4/4] 清理项目目录..."
    echo ""
    log_warn "⚠️  即将删除项目目录: ${PROJECT_DIR}"
    log_warn "    备份已保存到: ${BACKUP_DIR}"
    log_warn "    GitHub 上也有完整代码，可以随时 clone 回来"
    echo ""
    read -p "    确认删除? (输入 yes 继续): " CONFIRM
    if [ "$CONFIRM" = "yes" ]; then
        rm -rf "$PROJECT_DIR"
        log_ok "项目目录已删除"
    else
        log_info "已取消删除，项目目录保留"
    fi
else
    log_info "[4/4] 跳过（安全模式不删除项目目录，加 --purge 可启用）"
fi

# ==================== 完成 ====================
echo ""
echo "=========================================================="
echo -e "${GREEN}  Mac 本地停服 + 清理完成!${NC}"
echo "=========================================================="
echo ""
echo "  操作总结:"
echo "    已停止服务: ${STOPPED_COUNT} 个"
echo "    已删除 plist: ${REMOVED_COUNT} 个"
echo "    备份目录: ${BACKUP_DIR}"
if [ "$PURGE_MODE" = true ]; then
    echo "    清理模式: 已执行"
else
    echo "    清理模式: 未执行（项目目录保留）"
fi
echo ""
echo "  验证命令:"
echo "    launchctl list | grep xiaofei      # 应该无输出"
echo "    ls ~/Library/LaunchAgents/ | grep xiaofei  # 应该无输出"
echo ""
echo "  恢复方法:"
echo "    git clone git@github.com:mindywang19871129/xiaofeimao-cloud.git ~/WorkBuddy/2026-05-13-task-1"
echo "    cp ${BACKUP_DIR}/mistake_book.json ~/WorkBuddy/2026-05-13-task-1/"
echo ""
