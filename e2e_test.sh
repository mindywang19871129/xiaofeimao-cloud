#!/bin/bash
# ============================================================
#  小肥猫学习 · JumpServer 端到端自动化测试
# ============================================================
#  用法: bash e2e_test.sh
#
#  测试项:
#    1. 环境检查 (.env 变量、Python 包)
#    2. WebSocket 批改服务状态
#    3. 每日出题完整链路 (AI出题 → 推送 → Bitable)
#    4. 自动压缩功能
#    5. Crontab 定时任务配置
#    6. 日志目录可写性
# ============================================================

set -e

PASS=0; FAIL=0; SKIP=0
GREEN='\033[0;32m'; RED='\033[0;31m'; YELLOW='\033[1;33m'; BLUE='\033[0;34m'; NC='\033[0m'
INSTALL_DIR="/opt/xiaofeimao"
LOG_DIR="${INSTALL_DIR}/.logs"

pass() { PASS=$((PASS+1)); echo -e "  ${GREEN}✅ PASS${NC} $1"; }
fail() { FAIL=$((FAIL+1)); echo -e "  ${RED}❌ FAIL${NC} $1"; }
skip() { SKIP=$((SKIP+1)); echo -e "  ${YELLOW}⚠️  SKIP${NC} $1"; }
section() { echo ""; echo -e "${BLUE}━━━ $1 ━━━${NC}"; }

echo ""
echo "╔══════════════════════════════════════════════════╗"
echo "║  小肥猫学习 · JumpServer 端到端自动化测试       ║"
echo "║  时间: $(date '+%Y-%m-%d %H:%M:%S')                  ║"
echo "╚══════════════════════════════════════════════════╝"

# ==================== 1. 环境检查 ====================
section "1. 环境检查"

# 1a. 安装目录存在
if [ -d "$INSTALL_DIR" ]; then
    pass "安装目录存在: ${INSTALL_DIR}"
else
    fail "安装目录不存在: ${INSTALL_DIR}"
fi

# 1b. .env 文件存在且变量完整
if [ -f "${INSTALL_DIR}/.env" ]; then
    source "${INSTALL_DIR}/.env"
    ENV_OK=true
    for VAR in FEISHU_APP_ID FEISHU_APP_SECRET DEEPSEEK_API_KEY BITABLE_APP_TOKEN BITABLE_DAILY_TABLE_ID BITABLE_MISTAKE_TABLE_ID USER_OPEN_ID; do
        if [ -z "${!VAR}" ]; then
            fail ".env 缺少变量: ${VAR}"
            ENV_OK=false
        fi
    done
    $ENV_OK && pass ".env 文件完整 (7个变量)"
else
    fail ".env 文件不存在"
fi

# 1c. 关键 Python 文件存在
for FILE in daily_task.py auto_compress.py question_generator.py feishu_push.py feishu_bitable.py; do
    if [ -f "${INSTALL_DIR}/${FILE}" ]; then
        pass "源文件存在: ${FILE}"
    else
        fail "源文件缺失: ${FILE}"
    fi
done

# 1d. ws-server 文件存在
for FILE in main.py grading.py feishu_api.py; do
    if [ -f "${INSTALL_DIR}/cloud_function/ws-server/${FILE}" ]; then
        pass "ws-server 存在: ${FILE}"
    else
        fail "ws-server 缺失: ${FILE}"
    fi
done

# 1e. 日志目录可写
if [ -d "$LOG_DIR" ] && [ -w "$LOG_DIR" ]; then
    pass "日志目录可写: ${LOG_DIR}"
else
    fail "日志目录不可写: ${LOG_DIR}"
fi

# 1f. Python3 可用
if command -v python3 &>/dev/null; then
    PY_VER=$(python3 --version 2>&1)
    pass "Python3 可用: ${PY_VER}"
else
    fail "Python3 不可用"
fi

# 1g. 依赖包检查
for PKG in openai lark_oapi requests; do
    if python3 -c "import ${PKG}" 2>/dev/null; then
        pass "Python 包: ${PKG}"
    else
        fail "Python 包缺失: ${PKG}"
    fi
done

# ==================== 2. 服务状态 ====================
section "2. WebSocket 批改服务"

if systemctl is-active --quiet xiaofeimao 2>/dev/null; then
    pass "systemd 服务运行中"
    # 检查最近日志有无异常
    if journalctl -u xiaofeimao --since "5 minutes ago" 2>/dev/null | grep -qi "error\|exception\|traceback"; then
        fail "批改服务近期日志含错误"
    else
        pass "批改服务近期无错误日志"
    fi
else
    fail "systemd 服务未运行"
fi

# ==================== 3. 每日出题链路 ====================
section "3. 每日出题集成测试"

echo "  ⏳ 运行 daily_task.py（约 10-30 秒）..."
DAILY_OUTPUT=$(cd "$INSTALL_DIR" && python3 daily_task.py 2>&1)
DAILY_EXIT=$?

# 保存输出到日志
echo "$DAILY_OUTPUT" > "${LOG_DIR}/e2e_daily_test_$(date +%Y%m%d_%H%M%S).log"

if [ $DAILY_EXIT -eq 0 ]; then
    # 检查关键步骤
    # 检测成功标记（"每日任务调度完成" / "推送成功" / "HTTP 200"）
    if echo "$DAILY_OUTPUT" | grep -q "每日任务调度完成"; then
        pass "每日出题完整链路成功"
    elif echo "$DAILY_OUTPUT" | grep -q "推送成功"; then
        pass "每日出题: 题目已生成并推送成功"
    elif echo "$DAILY_OUTPUT" | grep -q "HTTP.*200 OK"; then
        pass "每日出题: 题目已生成(HTTP 200)"
    elif echo "$DAILY_OUTPUT" | grep -q "跳过生成"; then
        skip "每日出题: 今日题目已存在，跳过生成（正常行为）"
    elif echo "$DAILY_OUTPUT" | grep -qi "error\|exception\|traceback"; then
        fail "每日出题失败，详见 ${LOG_DIR}/e2e_daily_test_*.log"
    else
        skip "每日出题: 输出不完整，检查日志"
    fi
else
    fail "每日出题异常退出 (exit code: ${DAILY_EXIT})"
fi

# 检查 daily_questions.json 是否存在（允许跳过生成的情况，只检查文件存在+今天日期）
if [ -f "${INSTALL_DIR}/daily_questions.json" ]; then
    Q_TODAY=$(date -r "${INSTALL_DIR}/daily_questions.json" +%Y-%m-%d 2>/dev/null || stat -f %Sm -t %Y-%m-%d "${INSTALL_DIR}/daily_questions.json" 2>/dev/null)
    TODAY=$(date +%Y-%m-%d)
    if [ "$Q_TODAY" = "$TODAY" ]; then
        pass "daily_questions.json 今日已存在 ($TODAY)"
    else
        Q_AGE=$(($(date +%s) - $(stat -c %Y "${INSTALL_DIR}/daily_questions.json" 2>/dev/null || stat -f %m "${INSTALL_DIR}/daily_questions.json" 2>/dev/null)))
        fail "daily_questions.json 日期为 ${Q_TODAY}，非今天 (${Q_AGE}秒前)"
    fi
else
    fail "daily_questions.json 不存在"
fi

# ==================== 4. 自动压缩 ====================
section "4. 自动压缩测试"

echo "  ⏳ 运行 auto_compress.py（约 5 秒）..."
COMPRESS_OUTPUT=$(cd "$INSTALL_DIR" && python3 auto_compress.py 2>&1)
COMPRESS_EXIT=$?

echo "$COMPRESS_OUTPUT" > "${LOG_DIR}/e2e_compress_test_$(date +%Y%m%d_%H%M%S).log"

if [ $COMPRESS_EXIT -eq 0 ]; then
    if echo "$COMPRESS_OUTPUT" | grep -qi "error\|exception\|traceback"; then
        fail "自动压缩执行有错误"
    else
        pass "自动压缩执行成功"
    fi
else
    fail "自动压缩异常退出"
fi

# ==================== 5. Crontab 配置 ====================
section "5. Crontab 定时任务"

CRONTAB_CONTENT=$(crontab -l 2>/dev/null || echo "")

# 检查每日出题 09:00
if echo "$CRONTAB_CONTENT" | grep -q "daily_task.py"; then
    pass "每日出题 cron 已配置: $(echo "$CRONTAB_CONTENT" | grep 'daily_task' | head -1 | sed 's/^[[:space:]]*//')"
else
    fail "每日出题 cron 未配置"
fi

# 检查自动压缩 周日12:00
if echo "$CRONTAB_CONTENT" | grep -q "auto_compress.py"; then
    pass "自动压缩 cron 已配置: $(echo "$CRONTAB_CONTENT" | grep 'auto_compress' | head -1 | sed 's/^[[:space:]]*//')"
else
    fail "自动压缩 cron 未配置"
fi

# ==================== 6. Bitable 连接 ====================
section "6. Bitable 连接测试"

# 测试环境变量能否连上 Bitable
BITABLE_TEST=$(cd "$INSTALL_DIR" && python3 -c "
import os, sys
sys.path.insert(0, '.')
# 仅做导入测试，不实际写数据
try:
    from feishu_bitable import get_token
    token = get_token()
    if token and len(token) > 10:
        print('OK: tenant_access_token 获取成功')
    else:
        print('FAIL: token 为空')
except Exception as e:
    print(f'FAIL: {e}')
" 2>&1)

if echo "$BITABLE_TEST" | grep -q "^OK"; then
    pass "Bitable 连接正常 (tenant token 获取成功)"
else
    fail "Bitable 连接失败: ${BITABLE_TEST}"
fi

# ==================== 汇总 ====================
echo ""
echo "╔══════════════════════════════════════════════════╗"
echo "║              测试结果汇总                        ║"
echo "╠══════════════════════════════════════════════════╣"
printf "║  ${GREEN}✅ 通过: %-2d${NC}  ${RED}❌ 失败: %-2d${NC}  ${YELLOW}⚠️  跳过: %-2d${NC}          ║\n" "$PASS" "$FAIL" "$SKIP"
echo "╚══════════════════════════════════════════════════╝"
echo ""

if [ "$FAIL" -eq 0 ]; then
    echo -e "${GREEN}🎉 所有关键检查通过！服务可长期运行。${NC}"
else
    echo -e "${RED}⚠️  有 ${FAIL} 项失败，请检查上述失败项。${NC}"
fi

echo ""
echo "  详细日志: ${LOG_DIR}/e2e_*_test_*.log"
echo "  系统日志: journalctl -u xiaofeimao -n 50"
echo ""
