# 🐱 小肥猫 v2.2 — JumpServer 极简部署指南

> **原则**：你只需 git pull 一个分支，其余全部自动化。
> **仓库**：`git@github.com:mindywang19871129/xiaofeimao-cloud.git`

---

## 日常更新（一行命令）

登录 JumpServer Web 终端，输入：

```bash
cd /opt/xiaofeimao/cloud_function/ws-server && ./update.sh
```

这一条命令会自动完成：

```
停止服务 → git pull → 更新依赖 → 7项自动测试 → 启动服务 → 显示结果
```

**测试通过才启动服务，测试失败则保持现状并报告错误。**

---

## 首次部署（仅新服务器）

### 步骤 1：生成 SSH Key + 克隆

```bash
ssh-keygen -t ed25519 -C "xiaofeimao-server" -f ~/.ssh/id_ed25519_xiaofeimao -N ""
cat ~/.ssh/id_ed25519_xiaofeimao.pub
# ⬆ 复制公钥 → https://github.com/settings/keys → New SSH Key

cat >> ~/.ssh/config << 'EOF'
Host github.com
    HostName github.com
    User git
    IdentityFile ~/.ssh/id_ed25519_xiaofeimao
EOF
chmod 600 ~/.ssh/config
ssh -T git@github.com  # 验证连接

cd /opt
git clone git@github.com:mindywang19871129/xiaofeimao-cloud.git xiaofeimao
```

### 步骤 2：配置环境

```bash
cd /opt/xiaofeimao/cloud_function/ws-server
cp .env.example .env
vi .env   # 填入飞书、DeepSeek、Bitable 凭证
```

### 步骤 3：安装 + 配置 systemd

```bash
python3 -m venv venv
./venv/bin/pip install -r requirements.txt
chmod +x test.sh update.sh

useradd -r -s /bin/false xiaofeimao 2>/dev/null; true
chown -R xiaofeimao:xiaofeimao /opt/xiaofeimao
cp xiaofeimao.service /etc/systemd/system/
systemctl daemon-reload
systemctl enable xiaofeimao
./update.sh   # 自动测试 + 启动
```

---

## 自动测试清单（test.sh 执行 7 项）

| # | 测试项 | 验证内容 |
|---|--------|---------|
| 1 | Python 环境 | venv 存在、版本正确 |
| 2 | 模块导入 | `feishu_api` `grading` `question_generator` 可导入 |
| 3 | v2.2 特性 | `_detect_image_info` `content_type` `_process_image_batch` 存在 |
| 4 | 飞书 API | Token 获取成功 |
| 5 | 2026 教材 | 15 天循环完整，旧单元已删除 |
| 6 | 服务状态 | systemd 服务运行中 |
| 7 | 日志健康 | 最近日志无异常报错 |

---

## 端到端验证（手动）

部署完成后在飞书中测试：

1. 发「你好」→ 机器人回复 ✅
2. 发一张作答照片 → 收到批改结果 ✅
3. 发「进度」→ 收到学习进度卡片 ✅

观察 JumpServer 日志：

```bash
journalctl -u xiaofeimao -f
```

---

## 快速排障

```bash
# 服务起不来？
journalctl -u xiaofeimao --no-pager -n 30

# Token 获取失败？
cd /opt/xiaofeimao/cloud_function/ws-server
./venv/bin/python3 -c "from feishu_api import _get_tenant_token; print(_get_tenant_token()[:30])"

# 想回滚到上一个版本？
cd /opt/xiaofeimao && git log --oneline -3  # 看历史
git checkout <commit-hash>  # 切到旧版本
systemctl restart xiaofeimao
```

---

## 分支策略

- **`main`** — 稳定生产分支（JumpServer 始终追踪此分支）
- 开发/测试在本地完成 → push 到 main → JumpServer `./update.sh` 一键更新
