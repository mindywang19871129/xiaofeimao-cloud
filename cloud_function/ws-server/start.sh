#!/bin/bash
# ===================================
# 小肥猫 WebSocket 服务 - 启动脚本
# ===================================
# 用法：
#   chmod +x start.sh
#   ./start.sh
#
# 首次部署步骤：
#   1. cp .env.example .env
#   2. 编辑 .env 填入真实凭证
#   3. pip install -r requirements.txt
#   4. ./start.sh
# ===================================

set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR"

echo "===================================="
echo "🐱 小肥猫 WebSocket 服务启动脚本"
echo "===================================="

# 检查 Python
if ! command -v python3 &> /dev/null; then
    echo "❌ 未找到 python3，请先安装 Python 3.9+"
    exit 1
fi

# 加载 .env 文件（如果存在）
if [ -f ".env" ]; then
    echo "📋 加载 .env 配置..."
    set -a
    source .env
    set +a
else
    echo "⚠️  未找到 .env 文件，请从 .env.example 复制并配置"
    exit 1
fi

# 检查必要的环境变量
if [ -z "$FEISHU_APP_ID" ] || [ -z "$FEISHU_APP_SECRET" ]; then
    echo "❌ 缺少 FEISHU_APP_ID 或 FEISHU_APP_SECRET"
    exit 1
fi

if [ -z "$DEEPSEEK_API_KEY" ]; then
    echo "❌ 缺少 DEEPSEEK_API_KEY"
    exit 1
fi

# 创建虚拟环境（如果不存在）
if [ ! -d "venv" ]; then
    echo "📦 创建 Python 虚拟环境..."
    python3 -m venv venv
fi

# 激活虚拟环境并安装依赖
echo "📦 安装依赖..."
source venv/bin/activate
pip install -q -r requirements.txt

# 启动服务
echo "🚀 启动 WebSocket 长连接服务..."
echo "   按 Ctrl+C 停止"
echo ""
python3 main.py
