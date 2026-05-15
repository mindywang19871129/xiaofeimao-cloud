# 小肥猫 - GitHub 部署指南

## 架构

```
Mac 本地编辑 → git push → GitHub → 服务器 git pull → systemctl restart
```

## 一、首次设置（服务器上操作，仅需一次）

在 JumpServer 终端执行：

### 1. 生成 SSH Key（如果还没有）
```bash
ssh-keygen -t ed25519 -C "xiaofeimao-server" -f ~/.ssh/id_ed25519_xiaofeimao -N ""
cat ~/.ssh/id_ed25519_xiaofeimao.pub
```

### 2. 把公钥添加到 GitHub
复制上一步输出的公钥，打开 https://github.com/settings/keys → New SSH Key → 粘贴保存

### 3. 配置 SSH config
```bash
cat >> ~/.ssh/config << 'EOF'
Host github.com
    HostName github.com
    User git
    IdentityFile ~/.ssh/id_ed25519_xiaofeimao
EOF
chmod 600 ~/.ssh/config
```

### 4. 测试连接
```bash
ssh -T git@github.com
# 应该看到: Hi mindywang19871129! You've successfully authenticated...
```

## 二、初始化 Git 仓库（服务器上操作）

```bash
cd /opt/xiaofeimao/ws-server

# 停止服务
systemctl stop xiaofeimao

# 初始化 git
git init
git remote add origin git@github.com:mindywang19871129/xiaofeimao-cloud.git

# 保存 .env（防止被覆盖）
cp .env /tmp/xiaofeimao.env.bak

# 拉取最新代码
git fetch origin main
git reset --hard origin/main

# 恢复 .env
cp /tmp/xiaofeimao.env.bak .env

# 更新依赖
./venv/bin/pip install -q -r requirements.txt

# 启动服务
systemctl start xiaofeimao

# 检查状态
systemctl status xiaofeimao
journalctl -u xiaofeimao -f
```

## 三、日常更新流程

以后每次在 Mac 上改完代码 push 到 GitHub 后，在服务器上执行：

```bash
cd /opt/xiaofeimao/ws-server
systemctl stop xiaofeimao
git pull origin main
./venv/bin/pip install -q -r requirements.txt
systemctl start xiaofeimao
systemctl status xiaofeimao
```

### 一键更新脚本

可以保存为服务器上的 `~/update-xiaofeimao.sh`：

```bash
#!/bin/bash
cd /opt/xiaofeimao/ws-server
echo "🛑 停止服务..."
systemctl stop xiaofeimao
echo "📥 拉取最新代码..."
git pull origin main
echo "📦 更新依赖..."
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

## 四、Mac 本地开发流程

```bash
cd /Users/mindy/WorkBuddy/2026-05-13-task-1/cloud_function/ws-server

# 改代码...
# 测试导入
python3 -c "from feishu_api import *; print('OK')"

# 提交
cd ..  # 回到 cloud_function 目录
git add ws-server/
git commit -m "描述你的改动"
git push origin main

# 然后去 JumpServer 执行 ~/update-xiaofeimao.sh
```
