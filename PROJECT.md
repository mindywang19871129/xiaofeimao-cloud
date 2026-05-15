# 小肥猫学习·项目全貌

> 最后更新：2026-05-15 | 存档用途：避免每次加载大量上下文

## 一、项目概述

「小肥猫」是一个飞书 AI 学习机器人，为三年级孩子提供每日数学 + KET 英语练习，支持出题、批改、错题本全流程。

- **GitHub**: `mindywang19871129/xiaofeimao-cloud`
- **飞书应用名**: 小肥猫学习
- **AI**: DeepSeek API（出题 + 批改）
- **数据存储**: 飞书多维表格（Bitable）+ 本地 JSON

---

## 二、架构（最终版：全部在 JumpServer）

```
┌──────────────────────────────────────────────────────────┐
│            JumpServer 云服务器 (唯一运行节点)              │
│                                                          │
│  ┌─ systemd 常驻 ────────────────────────────────────┐  │
│  │  ws-server/main.py                                │  │
│  │  WebSocket 长连接 → 飞书事件 → 批改 → 回复卡片     │  │
│  └──────────────────────────────────────────────────┘  │
│                                                          │
│  ┌─ crontab 每天 09:00 ──────────────────────────────┐  │
│  │  daily_task.py                                    │  │
│  │  AI出题 → 飞书推送 → Bitable同步（周五三合一）     │  │
│  └──────────────────────────────────────────────────┘  │
│                                                          │
│  ┌─ crontab 每周日 12:00 ────────────────────────────┐  │
│  │  auto_compress.py                                 │  │
│  │  日志轮转 + 旧文件归档 + Memory精炼 + 错题本检查    │  │
│  └──────────────────────────────────────────────────┘  │
│                                                          │
│  数据流向：                                              │
│  出题 → daily_questions.json → Bitable                  │
│  批改 → Bitable读取题目 → DeepSeek批改 → 错题入库       │
│  错题 → mistake_book.json（本地）                        │
└──────────────────────────────────────────────────────────┘
```

**不再使用**：EdgeOne Pages 云函数（已废弃）、Mac 本地服务（已停用）。

---

## 三、完整文件清单

### 核心服务（JumpServer 运行）

| 文件 | 作用 | 运行方式 |
|------|------|----------|
| `cloud_function/ws-server/main.py` | WebSocket 长连接批改入口 | systemd 常驻 |
| `cloud_function/ws-server/feishu_api.py` | 飞书 SDK 封装（Bitable读写+消息发送） | 被 main.py 引用 |
| `cloud_function/ws-server/grading.py` | 批改核心逻辑（解析答案+AI批改+错题入库） | 被 main.py 引用 |
| `daily_task.py` | 每日出题调度（周一~周日出题+周五三合一） | crontab 09:00 |
| `question_generator.py` | AI 出题模块（调用 DeepSeek 生成题目） | 被 daily_task 调用 |
| `feishu_push.py` | 飞书卡片消息推送 | 被 daily_task 调用 |
| `feishu_bitable.py` | Bitable 建表/读写/初始化 | 被 daily_task/bitable_sync 调用 |
| `auto_compress.py` | 日志轮转+归档+Memory精炼+错题本检查 | crontab 周日12:00 |

### 配置与数据文件

| 文件 | 内容 |
|------|------|
| `cloud_function/ws-server/.env` | 飞书+DeepSeek+Bitable 凭证 |
| `daily_questions.json` | 当日题目缓存 |
| `weekend_bundle.json` | 周五三合一题目缓存 |
| `mistake_book.json` | 本地错题本（JSON） |
| `system_prompt.md` | AI 出题系统提示词 |
| `system_prompt_for_feishu_ai.md` | 飞书 AI 提示词 |
| `cloud_prompt_compact.md` | 精简版云函数提示词 |

### 已废弃/不再使用

| 文件 | 原因 |
|------|------|
| `bitable_sync.py` | Mac→Bitable 错题同步（不需要了，服务器直接写本地） |
| `bot_server.py` | Mac 本地批改（被 ws-server 替代） |
| `cloud_function/cloud-functions/index.py` | EdgeOne Pages 云函数（已废弃） |
| `cloud_function/vercel.json` | Vercel 配置（已废弃） |
| `deploy.sh` | 旧版部署脚本（替换为 deploy-all.sh） |
| `~/Library/LaunchAgents/com.xiaofeimao.*.plist` | Mac launchd 配置（已停用） |

### 脚本与文档

| 文件 | 作用 |
|------|------|
| `deploy-all.sh` | JumpServer 一键部署脚本 |
| `stop_and_clean.sh` | Mac 本地停服+清理脚本 |
| `cloud_function/ws-server/update.sh` | JumpServer 代码更新脚本（git pull + 重启） |
| `cloud_function/ws-server/start.sh` | 手动启动脚本 |
| `DEPLOY.md` | 旧架构部署文档（待更新） |
| `PROJECT.md` | 本文件——项目全貌文档 |

---

## 四、跳板机配置速查

### 服务器路径
```
/opt/xiaofeimao/
├── cloud_function/ws-server/   ← 批改服务
│   ├── main.py
│   ├── feishu_api.py
│   ├── grading.py
│   ├── .env
│   ├── venv/
│   └── requirements.txt
├── daily_task.py
├── question_generator.py
├── feishu_push.py
├── feishu_bitable.py
├── auto_compress.py
├── daily_questions.json
├── weekend_bundle.json
├── mistake_book.json
├── system_prompt.md
├── system_prompt_for_feishu_ai.md
├── cloud_prompt_compact.md
├── .logs/                      ← 日志目录
├── archive/                    ← 归档目录
│   ├── html/
│   └── misc/
└── .workbuddy/memory/          ← 记忆文件
```

### Systemd 服务
```bash
# 查看状态
systemctl status xiaofeimao

# 启停
systemctl start/stop/restart xiaofeimao

# 日志
journalctl -u xiaofeimao -f
```

### Crontab 定时任务
```bash
# 每天 09:00 出题推送
0 9 * * * /opt/xiaofeimao/run_tasks.sh daily

# 每周日 12:00 自动压缩
0 12 * * 0 /opt/xiaofeimao/run_tasks.sh compress
```

### 环境变量（.env）
> ⚠️ 以下为模板，实际凭证不提交到 Git。在 JumpServer 上通过 `deploy-all.sh` 自动写入。
```env
FEISHU_APP_ID=<YOUR_FEISHU_APP_ID>
FEISHU_APP_SECRET=<YOUR_FEISHU_APP_SECRET>
DEEPSEEK_API_KEY=<YOUR_DEEPSEEK_API_KEY>
BITABLE_APP_TOKEN=<YOUR_BITABLE_APP_TOKEN>
BITABLE_DAILY_TABLE_ID=<YOUR_DAILY_TABLE_ID>
BITABLE_MISTAKE_TABLE_ID=<YOUR_MISTAKE_TABLE_ID>
USER_OPEN_ID=<YOUR_USER_OPEN_ID>
```

---

## 五、凭证速查（不提交 Git，仅供本地参考）

> ⚠️ 以下凭证存储在 Mac 本地文件，**不提交到 GitHub**。  
> 部署到 JumpServer 时通过 `deploy-all.sh`（已加入 .gitignore）自动写入 `.env`。

| 项目 | 获取位置 |
|------|----------|
| GitHub 仓库 | `git@github.com:mindywang19871129/xiaofeimao-cloud.git` |
| 飞书 App ID | 飞书开放平台 → 应用凭证 |
| 飞书 App Secret | 飞书开放平台 → 应用凭证 |
| DeepSeek API Key | DeepSeek 开放平台 → API Keys |
| Bitable App Token | 飞书多维表格 → 高级权限 |
| 每日题目表 ID | Bitable 表 URL 中提取 |
| 错题本表 ID | Bitable 表 URL 中提取 |
| 用户 Open ID | 飞书开放平台 → 用户信息 |
| 飞书事件订阅方式 | WebSocket 长连接（飞书后台配置） |

---

## 六、日常运维

### 更新代码
```bash
# 在 JumpServer 上
cd /opt/xiaofeimao/cloud_function/ws-server
./update.sh
```

### 查看服务状态
```bash
systemctl status xiaofeimao
crontab -l | grep xiaofeimao
```

### Mac 本地（开发用）
```bash
cd /Users/mindy/WorkBuddy/2026-05-13-task-1
git pull   # 同步最新代码
# 改代码后
git add -A && git commit -m "描述改动" && git push origin main
# 然后到 JumpServer 执行 update.sh
```
