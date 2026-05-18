#!/bin/bash
# ===================================
# 小肥猫 JumpServer 一键更新脚本（含自动测试）
# 用法：在 JumpServer 上执行
#   cd /opt/xiaofeimao/cloud_function/ws-server
#   ./update.sh
# ===================================
set -e

REPO_ROOT="/opt/xiaofeimao"
WS_DIR="${REPO_ROOT}/cloud_function/ws-server"

echo "🛑 停止服务..."
systemctl stop xiaofeimao

echo "📥 同步远端代码..."
cd "${REPO_ROOT}"
git fetch origin main
git reset --hard origin/main

echo "📦 更新依赖..."
cd "${WS_DIR}"
./venv/bin/pip install -q -r requirements.txt

echo "🧪 运行自动化测试..."
chmod +x test.sh
if ./test.sh; then
    echo ""
    echo "✅ 测试全部通过，启动服务..."
else
    echo ""
    echo "❌ 测试失败！请检查以上错误后重试。"
    echo "   服务未启动，当前状态:"
    systemctl status xiaofeimao --no-pager
    exit 1
fi

echo "🚀 启动服务..."
systemctl start xiaofeimao
sleep 2

echo ""
echo "📋 服务状态:"
systemctl status xiaofeimao --no-pager

echo ""
echo "📜 最近 10 条日志:"
journalctl -u xiaofeimao --no-pager -n 10

echo ""
echo "====================================="
echo "🎉 更新完成！小肥猫 v2.2 已就绪"
echo "====================================="
