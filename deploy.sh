#!/bin/bash
# 小肥猫一键部署
# JumpServer 上只需执行：bash /opt/xiaofeimao/deploy.sh
set -e
cd /opt/xiaofeimao
git pull origin main
chmod +x cloud_function/ws-server/update.sh cloud_function/ws-server/test.sh 2>/dev/null; true
bash cloud_function/ws-server/update.sh
