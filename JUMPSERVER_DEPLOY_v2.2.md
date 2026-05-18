# 🐱 小肥猫 v2.2 — JumpServer Web 终端部署指南

> **适用场景**：通过 JumpServer Web 终端远程部署到云服务器
> **更新日期**：2026-05-18
> **版本**：v2.2
> **仓库**：`git@github.com:mindywang19871129/xiaofeimao-cloud.git`

---

## 前置检查

在 JumpServer Web 终端中，先确认服务器环境：

```bash
# 1. 确认系统版本
cat /etc/os-release | head -3

# 2. 确认 Python 版本（需要 3.9+）
python3 --version

# 3. 确认磁盘空间
df -h /opt
```

---

## 一、首次部署（全新服务器）

> 如果服务器上**已有 v2.1 部署**，跳到第二章「更新到 v2.2」。

### 步骤 1：配置 GitHub SSH 访问

```bash
# 生成专用 SSH Key
ssh-keygen -t ed25519 -C "xiaofeimao-server" -f ~/.ssh/id_ed25519_xiaofeimao -N ""

# 显示公钥（复制整行输出）
cat ~/.ssh/id_ed25519_xiaofeimao.pub
```

然后：
1. 打开 https://github.com/settings/keys
2. 点击 **New SSH Key**
3. Title 填 `xiaofeimao-JumpServer`，Key 粘贴刚才复制的公钥
4. 保存

回到 JumpServer 终端验证：

```bash
# 配置 SSH config
cat >> ~/.ssh/config << 'EOF'
Host github.com
    HostName github.com
    User git
    IdentityFile ~/.ssh/id_ed25519_xiaofeimao
EOF
chmod 600 ~/.ssh/config

# 测试连接（应显示 "Hi mindywang19871129! ..."）
ssh -T git@github.com
```

### 步骤 2：克隆仓库

```bash
cd /opt
git clone git@github.com:mindywang19871129/xiaofeimao-cloud.git xiaofeimao
```

### 步骤 3：配置环境变量

```bash
cd /opt/xiaofeimao/cloud_function/ws-server

# 从模板创建 .env
cp .env.example .env

# 编辑 .env（填入真实凭证）
vi .env
```

`.env` 内容模板（替换 `xxx` 为真实值）：

```ini
FEISHU_APP_ID=cli_aa8f8d25a925dbea
FEISHU_APP_SECRET=9vyD11qA4jIxn3PCQB1jnfvTXMXs2Rve
DEEPSEEK_API_KEY=sk-f5d41971d21d46ffbdd4e1d7af4a093c
BITABLE_APP_TOKEN=Kppxb5S0SaYnEAsZgaFcXwIDnIf
BITABLE_DAILY_TABLE_ID=tblemc8rI6lvypbf
BITABLE_MISTAKE_TABLE_ID=tblcqp7Dec8TnUjc
USER_OPEN_ID=ou_8bf3770ed43ce0f273c7a34f1597cfe9
```

### 步骤 4：安装 Python 依赖

```bash
cd /opt/xiaofeimao/cloud_function/ws-server

python3 -m venv venv
./venv/bin/pip install -r requirements.txt
```

预期输出：
```
Successfully installed lark-oapi-1.x.x openai-1.x.x ...
```

### 步骤 5：配置 systemd 服务

```bash
# 复制服务文件
cp /opt/xiaofeimao/cloud_function/ws-server/xiaofeimao.service /etc/systemd/system/

# 如果服务器没有 xiaofeimao 用户，创建一个
useradd -r -s /bin/false xiaofeimao 2>/dev/null || echo "用户已存在，跳过"

# 确保目录权限
chown -R xiaofeimao:xiaofeimao /opt/xiaofeimao

# 注册并启动
systemctl daemon-reload
systemctl enable xiaofeimao
systemctl start xiaofeimao

# 检查状态（应显示 active (running)）
systemctl status xiaofeimao --no-pager
```

### 步骤 6：验证服务

```bash
# 查看实时日志（Ctrl+C 退出）
journalctl -u xiaofeimao -f

# 应该看到类似输出：
# 🐱 小肥猫 WebSocket 长连接模式启动
# ✅ 已建立 WebSocket 长连接
# ✅ 服务就绪，等待消息事件...
```

---

## 二、更新到 v2.2（已有部署的服务器）

> 如果已经部署了 v2.1，执行以下命令更新。

```bash
# 一键更新（直接逐行复制粘贴）
cd /opt/xiaofeimao && \
systemctl stop xiaofeimao && \
echo "✅ 服务已停止" && \
git pull origin main && \
echo "✅ 代码已更新" && \
cd cloud_function/ws-server && \
./venv/bin/pip install -q -r requirements.txt && \
echo "✅ 依赖已更新" && \
systemctl start xiaofeimao && \
echo "✅ 服务已启动" && \
sleep 2 && \
systemctl status xiaofeimao --no-pager && \
echo "" && \
echo "📜 最近 10 条日志:" && \
journalctl -u xiaofeimao --no-pager -n 10
```

或者使用已有的 `update.sh` 脚本：

```bash
cd /opt/xiaofeimao/cloud_function/ws-server
chmod +x update.sh
./update.sh
```

---

## 三、确认 v2.2 关键代码已生效

```bash
# 1. 确认图片格式检测函数存在
grep "_detect_image_info" /opt/xiaofeimao/cloud_function/ws-server/feishu_api.py

# 2. 确认 Token 复用逻辑
grep "_get_tenant_token" /opt/xiaofeimao/cloud_function/ws-server/main.py

# 3. 确认多图片批次逻辑
grep "content_type" /opt/xiaofeimao/cloud_function/ws-server/main.py | head -5

# 4. 确认 2026 教材
grep "动物体重" /opt/xiaofeimao/question_generator.py
```

如果以上 4 条都有输出 → v2.2 代码已正确部署 ✅

---

## 四、端到端功能测试

### 测试 A：服务健康检查

```bash
# 查看进程是否运行
ps aux | grep main.py | grep -v grep

# 查看日志确认 WebSocket 已连接
journalctl -u xiaofeimao --no-pager -n 20 | grep -i "websocket\|连接\|就绪"
```

预期：看到 `WebSocket 长连接已建立` 或 `服务就绪`

### 测试 B：飞书事件回调验证

1. 打开 [飞书开放平台](https://open.feishu.cn/app)
2. 找到「小肥猫学习」应用
3. 左侧菜单 → **事件与回调**
4. 确认订阅方式为「使用长连接接收事件」
5. 确认订阅事件包含 `im.message.receive_v1`

### 测试 C：基础消息回复（飞书端）

在飞书中给「小肥猫学习」机器人发消息：

```
你好
```

观察日志：

```bash
journalctl -u xiaofeimao -f
```

预期：日志显示收到消息事件，机器人回复了消息。

### 测试 D：单张图片批改

1. 在 Mac 上运行出题命令（或等到 09:00 定时推送）：

```bash
cd /Users/mindy/WorkBuddy/2026-05-18-task-10/xiaofeimao-cloud
python3 daily_task.py --force
```

2. 在飞书中，对当日学习卡片拍照发图片作为作答
3. 等待 5-10 秒，观察是否收到批改回复

**检查点**：
- 图片上传没有 400 错误（验证图片格式检测）
- 日志中看到 `📷 下载图片` 和 `image/...` 格式信息
- 收到批改结果卡片

```bash
# 观察日志
journalctl -u xiaofeimao --no-pager -n 30 | grep -i "图片\|image\|上传\|upload"
```

### 测试 E：多张图片批次批改

1. 连续发送 2-3 张不同题目的作答照片（间隔 < 60 秒）
2. 等待批次窗口（60 秒）结束
3. 观察是否收到汇总批改结果

**检查点**：
- 日志显示 `批次收集` 或 `batch` 相关日志
- 批改结果覆盖了所有图片

```bash
journalctl -u xiaofeimao --no-pager -n 50 | grep -i "批次\|batch\|多图"
```

### 测试 F：逐日追踪

1. 发送命令查看进度：

在飞书中给机器人发：
```
进度
```

预期：收到当前学习周期的完成进度卡片

### 测试 G：2026 教材出题验证

在 JumpServer 上直接测试出题模块：

```bash
cd /opt/xiaofeimao
source cloud_function/ws-server/venv/bin/activate

# 测试出题模块导入
python3 -c "
from question_generator import MATH_TOPICS
print(f'教材主题数: {len(MATH_TOPICS)}')
for i, t in enumerate(MATH_TOPICS):
    print(f'  Day {i+1}: {t[\"unit\"]} - {t[\"day_title\"]}')
"
```

预期输出 15 天循环，包含：
- 整数乘法（一）
- 图形的运动（二）
- 周长
- 制作动物体重说明书
- 整数除法（一）
- 动手做
- 数学好玩·图书排序
- 关系与规律
- 数据的整理与表示
- 家庭旅行计划

---

## 五、常用运维命令速查

```bash
# 查看服务状态
systemctl status xiaofeimao --no-pager

# 启动服务
systemctl start xiaofeimao

# 停止服务
systemctl stop xiaofeimao

# 重启服务
systemctl restart xiaofeimao

# 查看实时日志
journalctl -u xiaofeimao -f

# 查看最近 50 条日志
journalctl -u xiaofeimao --no-pager -n 50

# 查看今天的所有日志
journalctl -u xiaofeimao --since today --no-pager

# 搜索特定错误
journalctl -u xiaofeimao --no-pager | grep -i "error\|错误\|失败"

# 一键更新 + 重启
cd /opt/xiaofeimao && systemctl stop xiaofeimao && git pull origin main && cd cloud_function/ws-server && ./venv/bin/pip install -q -r requirements.txt && systemctl start xiaofeimao && systemctl status xiaofeimao --no-pager
```

---

## 六、故障排查

### 服务无法启动

```bash
# 查看详细错误
journalctl -u xiaofeimao --no-pager -n 30

# 手动运行看报错
cd /opt/xiaofeimao/cloud_function/ws-server
source venv/bin/activate
python3 main.py
```

常见原因：
- `.env` 文件不存在或凭证错误
- Python 包未安装：`./venv/bin/pip install -r requirements.txt`
- 端口被占用（WebSocket 模式不占用端口，一般不会）

### WebSocket 连接不上

```bash
# 测试飞书 API 连通性
source /opt/xiaofeimao/cloud_function/ws-server/venv/bin/activate
python3 -c "
from feishu_api import _get_tenant_token
token = _get_tenant_token()
print(f'Token 获取成功: {token[:20]}...')
"
```

如果 Token 获取失败 → 检查 `.env` 中的 `FEISHU_APP_ID` 和 `FEISHU_APP_SECRET`

### 图片批改无回复

```bash
# 查看图片处理相关日志
journalctl -u xiaofeimao --no-pager -n 100 | grep -i "图片\|image\|download\|upload"
```

检查：
- 飞书后台 → 事件订阅 → 确认 `im.message.receive_v1` 已订阅
- 确认 `DEEPSEEK_API_KEY` 正确
- 确认 Bitable 表 ID 正确

---

## 七、回滚到 v2.1

```bash
cd /opt/xiaofeimao
systemctl stop xiaofeimao
git checkout 9269074  # v2.1 最后一个 commit
systemctl start xiaofeimao
systemctl status xiaofeimao --no-pager
```

> 回滚后如需再切回 v2.2：`git checkout main && git pull`
