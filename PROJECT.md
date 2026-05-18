# 小肥猫学习·项目全貌

> 最后更新：2026-05-18 15:25 | 存档用途：避免每次加载大量上下文
> **v2.2 升级（2026-05-18）**：多图片批次处理 + 区段匹配部分批改 + 逐日强制完成 + 图片发送修复

## 一、项目概述

「小肥猫」是一个飞书 AI 学习机器人，为三年级孩子提供每日数学 + KET 英语练习，支持出题、批改、错题本全流程。

- **GitHub**: `mindywang19871129/xiaofeimao-cloud`
- **飞书应用名**: 小肥猫学习
- **AI**: DeepSeek API（出题 + 智能批改 + 规则解析）
- **数据存储**: 飞书多维表格（Bitable）+ 本地 JSON
- **v2 核心能力**：LLM智能答案解析 + 深度分析批改 + 飞书动态规则调整

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

## 二点五、智能批改系统 v2.1（2026-05-18 升级）

### 核心升级

| 模块 | v1（旧） | v2（新） |
|------|---------|---------|
| **答案解析** | 正则匹配3种固定格式 | LLM理解任意格式（句子/中文数字/混合） |
| **批改方式** | 精确匹配 → 失败才走AI | 每道题都由AI深度分析 |
| **评分** | 全对/全错 二级 | 满分/半对/全错 三级（score_ratio） |
| **错因分析** | 简单匹配判断 | AI分析思维过程+错因+改进建议 |
| **规则调整** | 写死在代码里，需重新部署 | 飞书回复「调整：XXX」自动生效 |

### 处理流程 v2

```
飞书消息
├── 修改建议（以"调整"/"修改"/"新增规则"等开头）
│   └── LLM解析 → 提取规则 → 存入 grading_rules.json → 确认回复
├── 查看规则指令
│   └── 列出所有当前活跃规则
├── 其他指令（查看错题本等）
│   └── 提示格式说明
└── 答案提交（文本或图片）
    ├── 图片 → OCR识别 → LLM提取答案
    ├── 文本 → LLM智能解析答案（理解非标准格式）
    └── LLM深度批改（注入当前规则）→ 分级评分 → 错题入库 → 结果卡片
```

### 动态规则系统

**规则存储**：`项目根目录/grading_rules.json`

**飞书调整方式**：
```
# 在飞书单聊中直接回复（会自动识别为修改建议，不触发批改）
调整：翻译题只要意思对就可以，不用逐字匹配
修改：数学答案写成中文也算对，比如"六十三页"
新增规则：单词拼写差1个字母算半对，不扣全分
删除规则：rule_20260518_1
查看规则
```

**系统自动**：
1. LLM 解析自然语言修改建议
2. 提取：科目、规则类型、规则文本
3. 存入 grading_rules.json
4. 下次批改自动加载生效（无需重启服务）

**内置默认规则**（grading_rules.json v2.1，共10条）：
1. 核心意思一致即可给分，不要求逐字匹配
2. 非标准格式先理解含义再判断，但语义错该扣就扣
3. 分级评分：全分/半分/零分
4. 英语拼写差1个字母标半对，考拼写则全错
5. **【KET语法硬标准】时态错误/词形变种/主谓不一致 → 直接判错(0.0)，必须指出错误点+正确形式+案例**
6. **【KET评分标准】时态/语态/主谓一致为核心得分点，扣分必须附带解释和案例**
7. **【小学数学计算标准】结果正确全分，方法正确计算错半分，展示正确步骤**
8. **【小学数学应用题标准】列式+计算+答案+单位全对才满分，缺项扣分，给出完整解题示范**
9. 翻译题看语义，但时态和语态必须正确
10. 每道错题必须输出：错因+正确答案+案例示范+记忆技巧

### 关键代码变更

| 文件 | 变更 |
|------|------|
| `grading.py` | 重写：新增 `parse_answers_with_ai()`、`deep_grade_with_ai()`（v2.1含KET/数学考试标准）、`process_modification_suggestion()`、`detect_modification_suggestion()`、`load_grading_rules()`、`_ket_scoring_standard()`、`_math_scoring_standard()` |
| `main.py` | 新增修改建议检测（步骤3）+ 查看规则指令（步骤4）+ 指令拦截（步骤5）+ 批改（步骤6） |
| `grading_rules.json` | 新增：动态规则存储文件，v2.1升级为10条（含KET和数学考试标准） |
| `format_grading_card()` | 增强：显示分级评分（🔶半对）、思维分析、改进建议、**案例示范(🌟)** |

### 部署注意

部署 v2 时需要将 `grading_rules.json` 同步到 JumpServer：
```bash
scp grading_rules.json jump-server:/opt/xiaofeimao/grading_rules.json
```
或直接在 JumpServer 上通过 `deploy-all.sh` 自动同步。

---

## 二点六、v2.2 多图片批次处理系统（2026-05-18 新增）

### 核心升级

| 模块 | v2.1（旧） | v2.2（新） |
|------|---------|---------|
| **图片发送** | 仅支持接收图片，无发送能力 | 新增 `upload_feishu_image()` + `send_feishu_image()` + 批量发送 |
| **多图片处理** | 单张图片→全部题目 | 60秒批次收集→逐张OCR→区段匹配→部分批改 |
| **题目匹配** | 整图识别全部答案 | AI区段匹配：每张图→对应题目区段 |
| **部分批改** | 不支持 | 已答题目自动批改，未答题目提示补充 |
| **进度追踪** | 无 | `daily_progress.json` 记录每日哪些题已批改 |
| **逐日强制** | 无 | 必须完成 Day N 才能开始 Day N+1 |

### 图片批次处理流程 v2.2

```
飞书发送多张图片
├── 第1张 → 加入批次 → 启动60秒定时器
├── 第2张 → 加入批次 → 重置定时器
├── ...
├── 定时器到期 → _process_image_batch()
│   ├── 1. 逐日前置检查（check_previous_day_completion）
│   │   └── 未完成前一天 → ⛔ 阻止，提示剩余题目
│   ├── 2. 逐张 DeepSeek Vision OCR
│   ├── 3. AI 区段匹配（_match_sections_to_questions）
│   │   └── 图1 → [M1, M2, M3] / 图2 → [E1, E2, E3, E4]
│   ├── 4. 按区段提取答案 + 深度批改
│   ├── 5. 标记已批改题目到 daily_progress.json
│   └── 6. 输出：已批改结果 + 剩余待完成题目列表
```

### 逐日强制完成规则

- **规则**：必须完成 Day N-1 的所有题目，才能开始 Day N
- **检查时机**：每次提交答案（文本或图片）前
- **阻止消息**：显示前一天日期 + 剩余未完成题目
- **进度查询**：回复「查看进度」或「完成情况」
- **完成标志**：当日的所有题目都已被批改（不论对错）

### 新增/修改文件

| 文件 | 变更 |
|------|------|
| `feishu_api.py` | **新增**：`upload_feishu_image()`、`send_feishu_image()`、`send_feishu_images_batch()`、`_get_tenant_token()` |
| `main.py` | **重写图片处理**：图片批次收集（60秒窗口）+ `_process_image_batch()` + 逐日检查 + 进度指令 |
| `grading.py` | **新增**：`grade_submission_multi_image()`、`format_partial_grading_card()`、`check_previous_day_completion()`、`get_daily_progress()`、`mark_questions_graded()`、`set_daily_total_questions()`、`_match_sections_to_questions()`、`_extract_answers_by_section()` |
| `daily_task.py` | **新增**：出题后自动调用 `set_daily_total_questions()` 初始化进度 |
| `daily_progress.json` | **新增**：每日进度存储文件 |
| `PROJECT.md` | **更新**：v2.2 文档 |

### 部署命令（JumpServer）

```bash
# 1. 拉取最新代码
cd /opt/xiaofeimao && git pull origin main

# 2. 重启批改服务（如果改了 ws-server 代码）
systemctl restart xiaofeimao

# 3. 确认服务正常
systemctl status xiaofeimao
journalctl -u xiaofeimao -n 20 --no-pager
```

### 新增指令

| 指令 | 功能 |
|------|------|
| `查看进度` / `完成情况` / `今日进度` | 显示当日已完成/待完成题目数 |
| `今日题目` / `重新发题目` / `题目列表` | **从 Bitable 读取当日所有题目**（按科目分组，含题号、题型、分值、题干） |
| 发送多张图片 | 60秒内发送的图片归入同一批次，统一OCR+区段匹配+部分批改 |

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
| `grading_rules.json` | **v2新增**：动态批改规则存储（飞书调整自动写入） |
| `system_prompt.md` | AI 出题系统提示词 |
| `system_prompt_for_feishu_ai.md` | 飞书 AI 提示词（备用方案） |
| `cloud_prompt_compact.md` | 精简版提示词 v2（含智能批改说明） |

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
├── grading_rules.json          ← v2新增：动态规则
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

## 七、图片批改规则

### 触发方式
飞书单聊中发送图片消息（拍照的作业答案）即可触发批改，无需手动打文字。

### 处理流程
```
飞书图片消息（message_type=image）
  → 1. _download_image(image_key)：调用飞书 OpenAPI 下载图片二进制
     GET https://open.feishu.cn/open-apis/im/v1/images/{image_key}
     返回 Content-Type: image/*（二进制数据，非 JSON）
  → 2. _ocr_image(image_bytes)：DeepSeek vision 模型 OCR 识别答案文本
     模型: deepseek-chat（支持 vision）
     提示词：只提取答案，按题号顺序输出，空格分隔
  → 3. grade_submission(text, msg_date, image_key)：走正常批改流程
  → 4. save_mistake_to_bitable(..., image_key)：错题记录「来源图片」字段
```

### 关键实现细节

**图片下载（_download_image）**：
- 飞书图片下载 API **直接返回二进制图片数据**（Content-Type: image/*）
- 不能先 `resp.json()`，需先检查 Content-Type 判断是否为图片
- 非图片响应（如 JSON 错误）才尝试解析 JSON 错误码

**OCR 识别（_ocr_image）**：
- 使用 DeepSeek 的 `deepseek-chat` 模型（已支持 vision）
- 图片以 base64 编码通过 `image_url` 传入
- OCR 提示词：提取所有答案，按题号顺序，多选/多空用逗号分隔
- 识别结果直接作为答案文本走现有解析+批改流程

**错题来源追溯**：
- 错题本新增「来源图片」字段（文本类型，存储 image_key）
- 文本批改：image_key 为空字符串
- 图片批改：image_key 为飞书图片的唯一标识
- 可通过 `https://open.feishu.cn/open-apis/im/v1/images/{image_key}` 回查原图

### 消息处理优先级
1. **图片消息**（message_type=image）→ 下载+OCR+批改
2. **文本消息**（message_type=text）→ 提取文本+批改
3. **富文本消息**（message_type=post）→ 提取文本+批改
4. **其他类型** → 忽略并记录日志

### 环境要求
- 飞书 App 需订阅 `im.message.receive_v1` 事件（含图片类型）
- DeepSeek API Key（需支持 vision 能力的模型）
- 图片大小限制：飞书 API 限制 20MB 以内

## 八、日常运维

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

### Mac 本地（开发用 — CodeBuddy IDE）
```bash
cd /Users/mindy/WorkBuddy/2026-05-18-task-10/xiaofeimao-cloud
# 改代码（CodeBuddy AI 辅助）
git add -A && git commit -m "描述改动" && git push origin main
# 然后到 JumpServer Web 终端执行部署命令（见下方）
```

### JumpServer 部署（通过 Web 终端）
> ⚠️ mindy 通过 **Web 页面**访问 JumpServer 终端（非 SSH 客户端）。
> 每次 git push 后，AI 会给出需要粘贴到 Web 终端的命令。

**部署命令模板**（AI 每次根据改动给出精确命令）：
```bash
# 1. 拉取最新代码
cd /opt/xiaofeimao && git pull origin main

# 2. 重启批改服务（如果改了 ws-server 代码）
systemctl restart xiaofeimao

# 3. 确认服务正常
systemctl status xiaofeimao
journalctl -u xiaofeimao -n 20 --no-pager
```

**如果改了定时任务脚本**（daily_task.py / question_generator.py 等）：
```bash
# 验证定时任务配置
crontab -l | grep xiaofeimao
```

**如果改了 Bitable 表结构**（feishu_bitable.py 的 table definition）：
```bash
# 重新初始化 Bitable（⚠️ 会删除旧数据！）
cd /opt/xiaofeimao && python3 feishu_bitable.py --init --force
# 更新 .env 中的新表 ID
vim cloud_function/ws-server/.env
systemctl restart xiaofeimao
```

**如果首次部署 grading_rules.json（v2 新增）**：
```bash
# Mac 本地先提交到 Git
cd /Users/mindy/WorkBuddy/2026-05-18-task-10/xiaofeimao-cloud
git add grading_rules.json && git commit -m "v2: 新增动态批改规则系统" && git push origin main

# JumpServer 拉取
cd /opt/xiaofeimao && git pull origin main
systemctl restart xiaofeimao
```

## 九、端到端测试流程

每次部署后必须验证的测试路径：

### 测试 1：文本批改
```
1. 飞书单聊发送: M1=300 M2=④ M3=20;500 M4=16;16 E1=should,must,permission,rule
2. 预期: 几秒内收到批改卡片，显示得分率和每道题对错
3. 验证: Bitable 错题本有新记录
```

### 测试 2：图片批改
```
1. 飞书单聊发送一张手写答案的图片
2. 预期: 收到"🔍 正在识别图片中的答案..." → 识别结果 → 批改卡片
3. 验证: 错题本记录中「来源图片」字段非空
```

### 测试 3：指令拦截
```
1. 飞书单聊发送: 查看错题本
2. 预期: 收到指令提示，不触发批改
```

### 测试 4：每日出题
```
1. 在 JumpServer 执行: python3 daily_task.py --force
2. 预期: 飞书收到今日练习卡片，Bitable 每日题目表新增题目
3. 验证: curl 或 Bitable UI 查看记录数
```

### 测试 5：v2 智能答案解析（非标准格式）
```
1. 飞书单聊发送: 第一题83，第二题44，第三题一共63页剩下22页，第四题forget arrive plan
2. 预期: AI正确匹配各题答案，正常批改（不报格式错误）
3. 验证: 答案匹配正确，各题答案与题目ID对应
```

### 测试 6：v2 修改建议
```
1. 飞书单聊发送: 调整：翻译题意对即可，不用逐字匹配
2. 预期: 收到"正在解析修改建议..." → "规则已更新"确认
3. 飞书发送: 查看规则
4. 预期: 新规则出现在规则列表中
```

### 测试 7：v2 分级评分
```
1. 提交一道答案与标准答案相似但不完全一致的题目
2. 预期: 批改卡片中显示🔶半对，且解析说明何处正确、何处错误
3. 验证: 得分率计算正确（半对=半分）
```

## 十、环境变量速查（JumpServer .env）

```bash
# 路径: /opt/xiaofeimao/cloud_function/ws-server/.env
FEISHU_APP_ID=cli_aa8f8d25a925dbea
FEISHU_APP_SECRET=<见 bitable 文档>
DEEPSEEK_API_KEY=<见 DeepSeek 控制台>
BITABLE_APP_TOKEN=HA4Mba31Eaiz1DsCpn6cCHmonjb
BITABLE_DAILY_TABLE_ID=tblZs7lETr1CvOW6
BITABLE_MISTAKE_TABLE_ID=tblPKWO7tJVLXnmi
USER_OPEN_ID=ou_8bf3770ed43ce0f273c7a34f1597cfe9
```

### 如何更新环境变量
```bash
vim /opt/xiaofeimao/cloud_function/ws-server/.env
systemctl restart xiaofeimao
```
