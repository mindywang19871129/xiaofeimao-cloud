#!/bin/bash
# ===================================
# 小肥猫 GitHub 一键更新脚本
# 用法：./update.sh
# 放到服务器的 /opt/xiaofeimao/ws-server/update.sh
# ===================================
set -e

cd /opt/xiaofeimao/ws-server

echo "🛑 停止服务..."
systemctl stop xiaofeimao

echo "📥 拉取最新代码..."
git pull origin main

echo "📦 更新依赖..."
./venv/bin/pip install -q -r requirements.txt

echo "🚀 启动服务..."
systemctl start xiaofeimao

echo ""
echo "📋 服务状态:"
systemctl status xiaofeimao --no-pager

echo ""
echo "📜 最近 10 条日志:"
journalctl -u xiaofeimao --no-pager -n 10
