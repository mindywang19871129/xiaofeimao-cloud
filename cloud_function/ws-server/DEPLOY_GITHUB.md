# 小肥猫 - GitHub 部署指南

## 仓库结构（v2.0）

```
GitHub: mindywang19871129/xiaofeimao-cloud
|
├── cloud_function/
│   ├── ws-server/           ← JumpServer 批改服务（WebSocket 长连接）
│   │   ├── main.py          ← 主入口
│   │   ├── feishu_api.py    ← 飞书 API 封装
│   │   ├── grading.py       ← 批改核心逻辑
│   │   ├── start.sh         ← 启动脚本
│   │   └── update.sh        ← 一键更新脚本
│   ├── cloud-functions/     ← EdgeOne Pages 云函数（HTTP 回调）
│   └── api/                 ← Vercel API 路由
├── daily_task.py            ← 每日出题调度（Mac / 服务器）
├── question_generator.py    ← AI 出题模块
├── feishu_push.py           ← 飞书消息推送
├── feishu_bitable.py        ← 多维表格操作
├── bitable_sync.py          ← 错题同步（Bitable → 本地）
├── auto_compress.py         ← 自动压缩清理
├── bot_server.py            ← 本地批改服务（备用，已停用）
├── system_prompt.md         ← AI 系统提示词
├── daily_questions.json     ← 今日题目缓存
└── .gitignore               ← 排除敏感文件
```

## 一、JumpServer 首次设置

### 1. 生成 SSH Key（如果还没有）
```bash
ssh-keygen -t ed25519 -C "xiaofeimao-server" -f ~/.ssh/id_ed25519_xiaofeimao -N ""
cat ~/.ssh/id_ed25519_xiaofeimao.pub
```

### 2. 把公钥添加到 GitHub
打开 https://github.com/settings/keys → New SSH Key → 粘贴保存

### 3. 配置 SSH config
```bash
cat >> ~/.ssh/config << 'EOF'
Host github.com
    HostName github.com
    User git
    IdentityFile ~/.ssh/id_ed25519_xiaofeimao
EOF
chmod 600 ~/.ssh/config
ssh -T git@github.com
```

### 4. 克隆仓库到服务器
```bash
# ⚠️ 新结构：Git 根在项目根，ws-server 在 cloud_function/ws-server/
cd /opt
git clone git@github.com:mindywang19871129/xiaofeimao-cloud.git xiaofeimao
cd /opt/xiaofeimao/cloud_function/ws-server

# 创建 .env 文件
cp .env.example .env
vim .env   # 填入真实的飞书、DeepSeek、Bitable 凭证

# 安装依赖
python3 -m venv venv
./venv/bin/pip install -r requirements.txt
```

### 5. 配置 systemd 服务
```bash
cp xiaofeimao.service /etc/systemd/system/
# 编辑 /etc/systemd/system/xiaofeimao.service
# 确认 WorkingDirectory 指向 /opt/xiaofeimao/cloud_function/ws-server
# 确认 ExecStart 指向 /opt/xiaofeimao/cloud_function/ws-server/venv/bin/python3 main.py

systemctl daemon-reload
systemctl enable xiaofeimao
systemctl start xiaofeimao
systemctl status xiaofeimao
```

## 二、JumpServer 日常更新

```bash
cd /opt/xiaofeimao
systemctl stop xiaofeimao
git pull origin main
cd cloud_function/ws-server
./venv/bin/pip install -q -r requirements.txt
systemctl start xiaofeimao
systemctl status xiaofeimao --no-pager
```

### 一键更新脚本（推荐）
在服务器上保存为 `~/update-xiaofeimao.sh`：

```bash
#!/bin/bash
cd /opt/xiaofeimao
echo "🛑 停止服务..."
systemctl stop xiaofeimao
echo "📥 拉取最新代码..."
git pull origin main
echo "📦 更新依赖..."
cd cloud_function/ws-server
./venv/bin/pip install -q -r requirements.txt
echo "🚀 启动服务..."
systemctl start xiaofeimao
echo "📋 服务状态:"
systemctl status xiaofeimao --no-pager
echo ""
echo "📜 最近日志:"
journalctl -u xiaofeimao --no-pager -n 10
```

使用：
```bash
chmod +x ~/update-xiaofeimao.sh
~/update-xiaofeimao.sh
```

## 三、Mac 本地开发流程

```bash
# 项目目录就是 Git 仓库根目录
cd /Users/mindy/WorkBuddy/2026-05-13-task-1

# 改代码...（直接编辑项目中的文件）
vim daily_task.py
vim question_generator.py
vim cloud_function/ws-server/grading.py

# 测试
python3 -c "from question_generator import *; print('OK')"

# 提交并推送
git add -A
git commit -m "描述你的改动"
git push origin main

# JumpServer 更新：登录 JumpServer 执行 ~/update-xiaofeimao.sh
```

## 四、Mac 首次拉取（clone）

如果换了新 Mac 或需要重新拉取：

```bash
cd /Users/mindy/WorkBuddy
git clone git@github.com:mindywang19871129/xiaofeimao-cloud.git 2026-05-13-task-1

# 创建本地配置文件（.gitignore 已排除）
cd 2026-05-13-task-1
cp deploy.sh.example deploy.sh
vim deploy.sh  # 填入真实 API Key

# 恢复 launchd 定时任务（如需本地出题）
# 参考 DEPLOY.md 中的 launchd 配置
```

## 五、飞书后台配置

飞书开放平台 → 小肥猫学习应用 → 事件与回调：
- **订阅方式**：使用长连接接收事件
- **订阅事件**：`im.message.receive_v1`
- **权限**：`im:message`、`im:message:send_as_bot`、`bitable:app`
