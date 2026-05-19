#!/bin/bash
# 小肥猫一键部署（无冲突版）
# JumpServer 上只需执行：bash /opt/xiaofeimao/deploy.sh
set -e
cd /opt/xiaofeimao

echo "📥 同步远端代码（丢弃本地修改，.env 受 .gitignore 保护）..."
git fetch origin main
git reset --hard origin/main

chmod +x cloud_function/ws-server/update.sh cloud_function/ws-server/test.sh 2>/dev/null; true
bash cloud_function/ws-server/update.sh

echo ""
echo "⏰ 部署每日出题定时器..."
cp -f cloud_function/ws-server/xiaofeimao-daily.service /etc/systemd/system/
cp -f cloud_function/ws-server/xiaofeimao-daily.timer /etc/systemd/system/
systemctl daemon-reload
systemctl enable --now xiaofeimao-daily.timer

echo "  定时器状态:"
systemctl status xiaofeimao-daily.timer --no-pager -l | head -8
echo ""
echo "  下次触发时间:"
systemctl list-timers xiaofeimao-daily.timer --no-pager
