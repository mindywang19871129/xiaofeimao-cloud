#!/bin/bash
# ===================================
# 小肥猫 JumpServer 一键更新脚本
# 用法：在 JumpServer 上执行 ./update.sh
# 路径：/opt/xiaofeimao/cloud_function/ws-server/update.sh
# ===================================
set -e

# Git 根目录（仓库 clone 位置）
REPO_ROOT="/opt/xiaofeimao"
WS_DIR="${REPO_ROOT}/cloud_function/ws-server"

echo "🛑 停止服务..."
systemctl stop xiaofeimao

echo "📥 拉取最新代码..."
cd "${REPO_ROOT}"
git pull origin main

echo "📦 更新依赖..."
cd "${WS_DIR}"
./venv/bin/pip install -q -r requirements.txt

echo "🚀 启动服务..."
systemctl start xiaofeimao

echo ""
echo "📋 服务状态:"
systemctl status xiaofeimao --no-pager

echo ""
echo "📜 最近 10 条日志:"
journalctl -u xiaofeimao --no-pager -n 10
