#!/bin/bash
# ===================================
# 小肥猫 v2.2 自动化测试脚本
# 用法：在 JumpServer 上执行 ./test.sh
# 路径：/opt/xiaofeimao/cloud_function/ws-server/test.sh
# ===================================
set -e

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

PASS=0
FAIL=0
WARN=0

log_pass() { echo -e "  ${GREEN}[PASS]${NC} $*"; PASS=$((PASS+1)); }
log_fail() { echo -e "  ${RED}[FAIL]${NC} $*"; FAIL=$((FAIL+1)); }
log_warn() { echo -e "  ${YELLOW}[WARN]${NC} $*"; WARN=$((WARN+1)); }

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
cd "$SCRIPT_DIR"

echo "========================================="
echo "🐱 小肥猫 v2.2 自动化测试"
echo "========================================="
echo ""

# ---- 测试 1：Python 环境 ----
echo "📋 [1/7] Python 环境检查"
if [ -f "venv/bin/python3" ]; then
    PY_VER=$(./venv/bin/python3 --version 2>&1)
    log_pass "Python 虚拟环境: $PY_VER"
else
    log_fail "虚拟环境不存在，请先运行: python3 -m venv venv"
fi

# ---- 测试 2：模块导入 ----
echo ""
echo "📋 [2/7] 核心模块导入测试"
for mod in feishu_api grading question_generator; do
    if [ "$mod" = "question_generator" ]; then
        PYTHONPATH="${REPO_ROOT}" ./venv/bin/python3 -c "import $mod" 2>/dev/null && \
            log_pass "import $mod" || \
            log_fail "import $mod 失败"
    else
        ./venv/bin/python3 -c "import $mod" 2>/dev/null && \
            log_pass "import $mod" || \
            log_fail "import $mod 失败"
    fi
done

# ---- 测试 3：v2.2 关键函数 ----
echo ""
echo "📋 [3/7] v2.2 特性验证"
# 检查 _detect_image_info 存在
if grep -q "_detect_image_info" feishu_api.py; then
    log_pass "图片格式自动检测 (_detect_image_info) 已就绪"
else
    log_fail "缺少图片格式检测函数"
fi

# 检查 content_type 处理
if grep -q "content_type" main.py; then
    log_pass "content_type 处理逻辑已就绪"
else
    log_fail "缺少 content_type 处理"
fi

# 检查多图片批次
if grep -q "_process_image_batch" main.py; then
    log_pass "多图片批次收集已就绪"
else
    log_fail "缺少批次处理逻辑"
fi

# ---- 测试 4：飞书 API 连通性 ----
echo ""
echo "📋 [4/7] 飞书 API 连通性"
./venv/bin/python3 -c "
from feishu_api import _get_tenant_token
try:
    token = _get_tenant_token()
    print(f'  [PASS] 飞书 Token 获取成功 ({token[:20]}...)')
except Exception as e:
    print(f'  [FAIL] 飞书 Token 获取失败: {e}')
    exit(1)
" 2>&1
if [ $? -eq 0 ]; then
    PASS=$((PASS+1))
else
    FAIL=$((FAIL+1))
fi

# ---- 测试 5：2026 教材数据 ----
echo ""
echo "📋 [5/7] 2026 教材数据校验"
PYTHONPATH="${REPO_ROOT}" ./venv/bin/python3 -c "
from question_generator import MATH_TOPICS
import sys

total = len(MATH_TOPICS)
if total != 15:
    print(f'  [FAIL] 教材天数异常: 预期15天, 实际{total}天')
    sys.exit(1)

# 检查关键单元
keywords = ['整数乘法', '图形的运动', '周长', '动物体重', '整数除法', '动手做', '图书排序', '关系与规律', '数据', '家庭旅行']
found = [k for k in keywords if any(k in str(t) for t in MATH_TOPICS)]
missing = set(keywords) - set(found)
if missing:
    print(f'  [FAIL] 缺少教材单元: {missing}')
    sys.exit(1)

# 确认旧单元已删除
old_keywords = ['面积', '认识分数', '年月日']
for kw in old_keywords:
    for t in MATH_TOPICS:
        if kw in str(t):
            print(f'  [FAIL] 旧单元未删除: {kw}')
            sys.exit(1)

print(f'  [PASS] 教材数据正常: {total}天循环, 所有关键单元就位, 旧单元已清理')
" 2>&1
if [ $? -eq 0 ]; then
    PASS=$((PASS+1))
else
    FAIL=$((FAIL+1))
fi

# ---- 测试 6：服务状态 ----
echo ""
echo "📋 [6/7] systemd 服务状态"
if systemctl is-active --quiet xiaofeimao 2>/dev/null; then
    log_pass "xiaofeimao 服务运行中"
else
    log_warn "xiaofeimao 服务未运行（部署后会自动启动）"
fi

# ---- 测试 7：日志健康 ----
echo ""
echo "📋 [7/7] 日志健康检查"
if journalctl -u xiaofeimao --no-pager -n 5 2>/dev/null | grep -qi "error\|traceback\|exception"; then
    log_warn "最近日志中有错误信息，建议检查"
else
    log_pass "最近日志无异常"
fi

# ---- 结果汇总 ----
echo ""
echo "========================================="
echo "  测试结果汇总"
echo "========================================="
echo -e "  ${GREEN}通过: ${PASS}${NC}"
echo -e "  ${RED}失败: ${FAIL}${NC}"
echo -e "  ${YELLOW}警告: ${WARN}${NC}"
echo ""

if [ $FAIL -gt 0 ]; then
    echo -e "  ${RED}❌ 有 ${FAIL} 项测试失败，请检查后重试${NC}"
    exit 1
elif [ $WARN -gt 0 ]; then
    echo -e "  ${YELLOW}⚠️  全部通过，有 ${WARN} 项警告${NC}"
    exit 0
else
    echo -e "  ${GREEN}✅ 全部 ${PASS} 项测试通过!${NC}"
    exit 0
fi
