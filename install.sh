#!/usr/bin/env bash
# IT 知识库机器人 · Linux 一键安装
set -e
cd "$(dirname "$0")"

echo "============================================"
echo "  IT 知识库机器人 · 一键安装（Linux）"
echo "============================================"

PY=python3
if ! command -v $PY >/dev/null 2>&1; then
  echo "[1/4] 未检测到 Python3，尝试安装..."
  if command -v apt-get >/dev/null 2>&1; then
    sudo apt-get update -y && sudo apt-get install -y python3 python3-pip
  elif command -v yum >/dev/null 2>&1; then
    sudo yum install -y python3 python3-pip
  elif command -v dnf >/dev/null 2>&1; then
    sudo dnf install -y python3 python3-pip
  else
    echo "[错误] 未知包管理器，请手动安装 Python 3.8+"; exit 1
  fi
else
  echo "[1/4] 已检测到 $($PY --version)"
fi

echo "[2/4] 安装依赖..."
$PY -m pip install -r requirements.txt -q

echo "[3/4] 准备配置文件..."
[ -f .env ] || cp .env.example .env

echo "[4/4] 初始化知识库数据..."
$PY tools/init_data.py

cat <<'EOF'

============================================
  安装完成！
  1. 编辑 .env 填 Bot ID/Secret，KB_MOCK 改 0
  2. 启动：python3 -m app.main
     常驻：nohup python3 -m app.main > logs/run.log 2>&1 &
============================================
EOF
