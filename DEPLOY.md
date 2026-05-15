# 🐱 小肥猫学习·混合架构部署指南

## 架构概述

```
┌──────────────────────────────────────────────────────────┐
│                    混合批改架构 v2.0                       │
├──────────────────────────────────────────────────────────┤
│                                                          │
│  Mac 开机时 (09:00 daily / 开机自启)                       │
│  ├─ [0] bitable_sync.py 从 Bitable 同步错题               │
│  ├─ [1] question_generator 生成今日题目                    │
│  ├─ [2] 构建飞书卡片消息                                   │
│  ├─ [3] feishu_push 推送到飞书                            │
│  └─ [4] 题目同步到 Bitable（周五推三天）                    │
│                                                          │
│  Mac 运行中 (每30分钟)                                     │
│  └─ bitable_sync.py 检查并拉取新错题                       │
│                                                          │
│  Mac 关机时                                               │
│  ├─ 家长在飞书回复答案                                     │
│  ├─ 飞书事件 → EdgeOne Pages 云函数                       │
│  ├─ 云函数从 Bitable 读取题目                              │
│  ├─ DeepSeek 批改                                        │
│  ├─ 错题写入 Bitable 错题本                               │
│  └─ 飞书消息回复批改结果                                   │
│                                                          │
│  Mac 再次开机                                             │
│  └─ bitable_sync.py 自动拉取未同步错题 → 本地错题本        │
│                                                          │
└──────────────────────────────────────────────────────────┘
```

---

## 一、硬件/软件前提

| 项目 | 要求 |
|------|------|
| Mac | 每天 09:00 保持开机（至少5分钟） |
| 飞书开发者账号 | 已创建"小肥猫学习"应用 |
| EdgeOne Pages 账号 | 腾讯边缘部署（国内可访问、无需翻墙） |
| GitHub / Gitee 账号 | 用于 EdgeOne Pages 部署 |
| DeepSeek API Key | 用于 AI 出题和批改 |

---

## 二、部署步骤

### 步骤 1：初始化飞书多维表格

在 Mac 上运行：
```bash
cd /Users/mindy/WorkBuddy/2026-05-13-task-1
python3 feishu_bitable.py --init
```

这会：
- 创建「小肥猫学习·题目与错题」多维表格
- 建立两张表：每日题目 + 错题本
- 保存配置到 `bitable_config.json`

✅ 完成后你会看到多维表格的 URL 链接。

---

### 步骤 2：部署 EdgeOne Pages 云函数

> **为什么用 EdgeOne Pages？** Vercel 从国内访问会超时，EdgeOne Pages 是腾讯边缘平台，国内直连，延迟低。

#### 2.1 推送代码到 Git 仓库

代码已准备好，在 `cloud_function/` 目录中：

```bash
cd /Users/mindy/WorkBuddy/2026-05-13-task-1/cloud_function
git remote add origin <你的仓库地址>
git push -u origin main
```

支持的 Git 平台：GitHub、Gitee（码云）、GitLab、Coding 等。

#### 2.2 在 WorkBuddy 中连接 EdgeOne Pages

1. 打开 WorkBuddy 桌面版
2. 左侧边栏 → 连接器 → 找到「EdgeOne Pages」
3. 点击连接，授权腾讯云账号
4. 状态变为 `connected` 即完成

#### 2.3 在 EdgeOne Pages 中导入项目

1. 登录 [EdgeOne Pages 控制台](https://console.cloud.tencent.com/edgeone/pages)
2. 新建项目 → 导入 Git 仓库
3. 选择刚才推送的仓库
4. 构建设置：
   - **根目录**：留空（即仓库根目录）
   - **框架预设**：无（自定义）
   - **输出目录**：留空
   - **云函数目录**：`cloud-functions`（自动检测）
5. 点击部署

#### 2.4 设置环境变量

在 EdgeOne Pages 项目设置 → 环境变量中添加：

| 变量名 | 值 | 说明 |
|--------|-----|------|
| `DEEPSEEK_API_KEY` | `sk-f5d41971d21d46ffbdd4e1d7af4a093c` | DeepSeek API Key |
| `FEISHU_APP_ID` | `cli_aa8f8d25a925dbea` | 飞书应用 ID |
| `FEISHU_APP_SECRET` | `9vyD11qA4jIxn3PCQB1jnfvTXMXs2Rve` | 飞书应用 Secret |
| `FEISHU_VERIFICATION_TOKEN` | （从步骤4获得） | 飞书事件验证 Token |
| `BITABLE_APP_TOKEN` | `Kppxb5S0SaYnEAsZgaFcXwIDnIf` | 多维表格 app_token |
| `BITABLE_DAILY_TABLE_ID` | `tblemc8rI6lvypbf` | 每日题目表 ID |
| `BITABLE_MISTAKE_TABLE_ID` | `tblcqp7Dec8TnUjc` | 错题本表 ID |
| `USER_OPEN_ID` | `ou_8bf3770ed43ce0f273c7a34f1597cfe9` | 家长 open_id |

> ⚠️ 先不要填 `FEISHU_VERIFICATION_TOKEN`，等步骤 4 拿到后再补。

#### 2.5 验证部署

部署后会得到一个域名（如 `xiaofeimao-xxxx.edgeone.site`），用 curl 测试：

```bash
curl https://<你的edgeone域名>/
# 应返回: {"status":"ok","service":"小肥猫学习·云批改","version":"1.0.0"}
```

---

### 步骤 3：在飞书开放平台配置事件订阅

1. 打开 [飞书开放平台](https://open.feishu.cn/app) → 你的应用「小肥猫学习」
2. 进入「事件订阅」→ 配置请求网址
3. 请求网址填写：`https://<你的edgeone域名>/`（注意末尾是 `/`）
4. 添加事件：`im.message.receive_v1`（接收消息）
5. 保存后会得到一个 **Verification Token**，记下来
6. 回到 EdgeOne Pages 环境变量，把 `FEISHU_VERIFICATION_TOKEN` 补上

> ✅ 飞书会立即向你的云函数发送 URL 验证请求，验证通过后事件订阅生效。

---

### 步骤 4：完成飞书事件订阅验证

1. 确认 EdgeOne Pages 端点可访问：`curl https://<域名>/`
2. 飞书开放平台 → 事件订阅 → 点击保存
3. 飞书向云函数发送 challenge 请求 → 正确响应 → 验证通过 ✅
4. 回到 EdgeOne Pages → 环境变量 → 设置 `FEISHU_VERIFICATION_TOKEN`

---

### 步骤 5：测试端到端流程

#### 5.1 测试 Mac 端出题
```bash
cd /Users/mindy/WorkBuddy/2026-05-13-task-1
python3 daily_task.py --dry-run --force
```
确认：题目生成成功 → 推送到飞书卡片 ✅ → Bitable 有题目记录 ✅

#### 5.2 测试云函数批改
1. 在飞书中回复答案（如 `M1=83 M2=44 E1=forget`）
2. 等待几秒
3. 收到批改结果回复 ✅
4. 检查 Bitable 错题本 → 有错题记录 ✅

#### 5.3 测试同步
```bash
python3 bitable_sync.py
# 应显示同步了云函数产生的错题
python3 bitable_sync.py --status
# 查看同步状态
```

---

## 三、Mac 日常运行

### 自动运行（launchd 已配置）

| 任务 | plist | 触发方式 | 作用 |
|------|-------|----------|------|
| 每日出题推送 | `com.xiaofeimao.daily-learning` | 每天 09:00 | 生成题目 → 推送到飞书 → 同步到 Bitable |
| 错题同步 | `com.xiaofeimao.bitable-sync` | 开机时 + 每30分钟 | 从 Bitable 拉取云函数批改的错题 → 本地错题本 |
| 本地批改服务 | `com.xiaofeimao.bot-server` | 开机自启 | Mac 在线时的本地批改（备用） |
| 自动压缩 | `com.xiaofeimao.auto-compress` | 每周日 12:00 | 日志轮转、旧文件归档、Memory 精炼 |

### 查看日志
```bash
# 每日任务日志
tail -f .logs/daily_task.log

# Bitable 操作日志
tail -f .logs/bitable.log

# 同步日志
tail -f .logs/bitable_sync.log
```

### 手动操作
```bash
# 手动出题推送
python3 daily_task.py --force

# 仅查看 Bitable 状态
python3 feishu_bitable.py --list-unsynced

# 手动同步错题
python3 bitable_sync.py

# 查看同步状态
python3 bitable_sync.py --status
```

---

## 四、Bitable 配置速查

运行 `python3 feishu_bitable.py --init` 后，所有配置保存在 `bitable_config.json`:

```json
{
  "app_token": "S404b*****e9PQsYDWYcNryFn0g",
  "daily_table_id": "tbl********abc",
  "mistake_table_id": "tbl********def",
  "url": "https://example.feishu.cn/base/S404b*****e9PQsYDWYcNryFn0g"
}
```

在 Vercel 环境变量中使用对应的值。

---

## 五、关键文件清单

| 文件 | 作用 | 运行位置 |
|------|------|----------|
| `daily_task.py` | 每日调度主脚本（含周五三天套餐） | Mac |
| `question_generator.py` | AI 出题模块 | Mac |
| `feishu_push.py` | 飞书消息推送 | Mac |
| `feishu_bitable.py` | 多维表格操作（建表/读写） | Mac |
| `bitable_sync.py` | 错题同步（Bitable → 本地） | Mac |
| `bot_server.py` | 本地批改服务（备用） | Mac |
| `auto_compress.py` | 自动压缩与提纯 | Mac |
| `cloud_function/cloud-functions/index.py` | 云函数批改端点 | EdgeOne Pages |
| `bitable_config.json` | Bitable 配置（运行 --init 后生成） | 两端共用 |

### launchd 配置文件
| plist | 路径 |
|-------|------|
| 每日出题 | `~/Library/LaunchAgents/com.xiaofeimao.daily-learning.plist` |
| 错题同步 | `~/Library/LaunchAgents/com.xiaofeimao.bitable-sync.plist` |
| 批改服务 | `~/Library/LaunchAgents/com.xiaofeimao.bot-server.plist` |
| 自动压缩 | `~/Library/LaunchAgents/com.xiaofeimao.auto-compress.plist` |

---

## 六、故障排查

### Q: EdgeOne Pages 返回 500 错误
- 检查环境变量是否全部设置
- 查看 EdgeOne Pages 控制台 → 函数日志

### Q: 飞书事件订阅验证失败
- 确认 EdgeOne 端点返回 200（`curl https://<域名>/`）
- 检查 URL 末尾是否有 `/`
- 确认 `FEISHU_VERIFICATION_TOKEN` 正确

### Q: 收到消息后没有批改回复
- 检查 `BITABLE_APP_TOKEN` / `BITABLE_DAILY_TABLE_ID` 是否正确
- 确认当天题目已推送到 Bitable（检查 `daily_task.py` 日志）
- 查看 EdgeOne Pages 控制台 → 函数日志

### Q: Mac 关机后家长收不到批改
- 确认 EdgeOne Pages 服务正常运行
- 确认飞书事件订阅配置正确
- 确认题目已同步到 Bitable

### Q: Mac 开机后错题没有同步
- 检查 launchd 是否运行: `launchctl list | grep bitable-sync`
- 手动运行: `python3 bitable_sync.py` 查看错误
- 检查 `bitable_config.json` 是否存在
- 确认 Bitable 中有未同步的错题
- 查看日志: `tail -f .logs/bitable_sync.log`
