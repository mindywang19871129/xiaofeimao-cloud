# 小肥猫学习 · 图片批改规则

> 最后更新：2026-05-18 | 用途：运维参考 & AI 行为规范

---

## 一、触发条件

飞书单聊中发送**图片消息**（`message_type=image`）即可触发批改，无需任何文字。

飞书后台配置要求：
- 事件订阅方式：WebSocket 长连接
- 订阅事件：`im.message.receive_v1`（需订阅 image 类型）

---

## 二、处理流程

```
飞书图片消息（message_type=image）
  → 1. 发送"🔍 正在识别图片中的答案..."提示
  → 2. _download_image(image_key)：调用飞书 OpenAPI 下载图片二进制
  → 3. _ocr_image(image_bytes)：DeepSeek vision 模型 OCR 识别答案文本
  → 4. grade_submission(text, msg_date, image_key)：走正常批改流程
  → 5. 错题入库时记录 image_key（来源追溯）
  → 6. 返回批改卡片
```

### 步骤详解

**2. 图片下载（`_download_image`）—— 关键注意事项**

```python
# 飞书图片下载 API 直接返回二进制图片数据
# Content-Type: image/*（不是 JSON！）
GET https://open.feishu.cn/open-apis/im/v1/images/{image_key}

# ⚠️ 正确做法：先检查 Content-Type
content_type = resp.headers.get("Content-Type", "")
if "image" in content_type:
    return resp.content   # 直接返回二进制
# 非图片响应（JSON 错误）才尝试解析
```

**常见错误**：直接 `resp.json()` 会导致 JSON 解析失败，因为返回的是图片二进制数据。

**3. OCR 识别（`_ocr_image`）**

| 项目 | 配置 |
|------|------|
| 模型 | `deepseek-chat`（已支持 vision） |
| 图片传入方式 | base64 编码，通过 `image_url` 字段 |
| OCR 提示词 | "提取所有答案，按题号顺序输出，空格分隔" |
| 多选/多空 | 逗号分隔 |
| 温度 | 默认（不做特殊设置） |

**4. 批改流程**
- OCR 识别的文本直接作为答案，走与文本批改相同的流程
- 调 `grade_submission(feishu_client, text, msg_date, image_key=image_key)`

**5. 错题来源追溯**
- 错题本新增「来源图片」字段（文本类型，存储 `image_key`）
- 文本批改：`image_key` 为空字符串
- 图片批改：`image_key` 为飞书图片唯一标识
- 回查原图：`GET https://open.feishu.cn/open-apis/im/v1/images/{image_key}`

---

## 三、消息处理优先级

1. **图片消息**（`message_type=image`）→ 下载 + OCR + 批改
2. **文本消息**（`message_type=text`）→ 提取文本 + 批改
3. **富文本消息**（`message_type=post`）→ 提取文本 + 批改
4. **其他类型** → 忽略并记录日志

---

## 四、环境要求

| 项目 | 要求 |
|------|------|
| 飞书 App | 已发布，订阅 `im.message.receive_v1`（含 image 类型） |
| DeepSeek API Key | 需支持 vision 能力的模型（`deepseek-chat` 已支持） |
| 图片大小 | 飞书 API 限制 ≤ 20MB |
| 图片格式 | JPEG/PNG 均可 |

---

## 五、异常处理

| 场景 | 行为 |
|------|------|
| image_key 为空 | 直接返回，不处理 |
| 图片下载失败 | 返回"⚠️ 图片识别失败: {错误信息}" |
| OCR 返回空文本 | 返回"⚠️ 未能从图片中识别到答案文本，请拍照更清晰后重试" |
| DeepSeek API 未配置 | 返回空字符串，走降级批改 |
| 当日无题目 | 返回"📭 今天还没有题目记录" |
| 网络超时 | 下载/OCR 各自 30s 超时 |

---

## 六、运维命令

```bash
# 查看服务状态
systemctl status xiaofeimao

# 查看实时日志（观察图片批改进程）
journalctl -u xiaofeimao -f | grep -E '图片|OCR|image'

# 重启服务
systemctl restart xiaofeimao

# 查看错题本表（确认来源图片字段非空）
cd /opt/xiaofeimao && python3 -c "
from feishu_bitable import load_bitable_config, list_all_records
config = load_bitable_config()
records = list_all_records(config['app_token'], config['mistake_table_id'])
for r in records:
    f = r.get('fields', {})
    ik = f.get('来源图片', '')
    if ik:
        print(f'[{f.get(\"日期\")}] {f.get(\"题号\")} | 图片: {ik[:20]}...')
"
```

---

## 七、Bitable 表结构（错题本）

错题本表「来源图片」字段定义：

```python
{"field_name": "来源图片", "type": 1}  # 文本类型，存储 image_key
```

**注意**：如果 Bitable 表已存在但没有此字段，需要手动添加或重新初始化。

重新初始化 Bitable（⚠️ 会删除旧数据）：
```bash
cd /opt/xiaofeimao
python3 feishu_bitable.py --init --force
# 更新 .env 中的新表 ID
vim cloud_function/ws-server/.env
systemctl restart xiaofeimao
```

---

## 八、测试清单

部署后必须验证：

- [ ] 发送文本答案 → 收到批改卡片（基础功能正常）
- [ ] 发送手写答案图片 → 收到"正在识别" → 收到批改卡片
- [ ] 检查错题本 → 图片批改的错题「来源图片」字段非空
- [ ] 发送指令"查看错题本" → 收到指令提示（不触发批改）
- [ ] 发送空图片/模糊图片 → 适当错误提示

---

## 九、已知限制

- OCR 依赖 DeepSeek vision 模型，手写体识别准确率约 85-90%
- 图片中答案格式需整齐排列，歪斜/遮挡会影响识别
- 图片批改比文本批改多 2-3 秒延迟（下载+OCR）
- 飞书图片 API 有调用频率限制（约 100 次/分钟/应用）
