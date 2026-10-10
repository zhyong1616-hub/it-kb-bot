#!/usr/bin/env bash
# ============================================================
#  IT 知识库机器人 —— 发布新版本到公开更新仓库（运维侧脚本）
# ------------------------------------------------------------
#  用途：把当前这份代码打成一个新版本，推送到 Gitee / GitHub 公开仓库。
#        机器人所在电脑会定时自动拉取（KB_AUTO_UPDATE=1），实现"零操作升级"。
#
#  用法：
#     bash tools/push_update.sh 1.0.1 "修复：xxx"
#       $1 = 新版本号（必须比仓库里的大，格式 主.次.修）
#       $2 = 本次更新说明（会写进提交信息，也推送给机器人维护人）
#
#  前提：
#     - 当前目录已 git init，并已配置 remote origin 指向公开仓库
#     - 已配置推送凭据（HTTPS 用 PAT，或 SSH key）
#
#  说明：
#     - 只提交代码文件；.env / data/ 由 .gitignore 排除，不会外泄
#     - 推送成功后，机器人最多 KB_AUTO_UPDATE_INTERVAL 分钟内自动升级
# ============================================================
set -euo pipefail

NEW_VER="${1:-}"
NOTE="${2:-常规更新}"

if [ -z "$NEW_VER" ]; then
  echo "用法: bash tools/push_update.sh <新版本号> [更新说明]"
  echo "示例: bash tools/push_update.sh 1.0.1 \"修复：ERP 地址变更\""
  exit 1
fi

if ! printf '%s' "$NEW_VER" | grep -Eq '^[0-9]+\.[0-9]+\.[0-9]+$'; then
  echo "❌ 版本号格式不对，应为 主.次.修，例如 1.0.1"
  exit 1
fi

cd "$(dirname "$0")/.."

if [ ! -d .git ]; then
  echo "❌ 当前目录还不是 git 仓库。请先执行："
  echo "   git init -b main"
  echo "   git remote add origin <你的公开仓库地址>"
  exit 1
fi

if ! git remote get-url origin >/dev/null 2>&1; then
  echo "❌ 未配置 remote origin。请执行："
  echo "   git remote add origin <你的公开仓库地址>"
  exit 1
fi

OLD_VER="$(cat VERSION 2>/dev/null | tr -d ' \r\n' || echo '0.0.0')"
echo "当前版本: $OLD_VER  ->  新版本: $NEW_VER"

# 写版本号
printf '%s\n' "$NEW_VER" > VERSION

# 清掉不该入库的产物
find . -type d -name __pycache__ -prune -exec rm -rf {} + 2>/dev/null || true
rm -rf _backup _update_result.json 2>/dev/null || true

git add -A
if git diff --cached --quiet; then
  echo "ℹ️  没有文件变化，无需提交"
else
  git commit -m "release ${NEW_VER}: ${NOTE}" >/dev/null
  echo "✅ 已提交: release ${NEW_VER}: ${NOTE}"
fi

# 直接推送；若历史不干净，自动合并远端
if git push origin HEAD 2>/dev/null; then
  echo "✅ 已推送到 origin"
else
  echo "🔁 直推失败，尝试拉取合并后重推..."
  git pull --rebase --autostash origin "$(git rev-parse --abbrev-ref HEAD)"
  git push origin HEAD
  echo "✅ 合并后推送成功"
fi

echo ""
echo "============================================"
echo " 发布完成：$OLD_VER -> $NEW_VER"
echo " 机器人将在 ${KB_AUTO_UPDATE_INTERVAL:-10} 分钟内自动升级"
echo " 维护人（钟勇）会在企微收到升级完成通知"
echo "============================================"
