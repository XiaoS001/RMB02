#!/bin/sh
# RMB02 容器入口
#
# 🔴 核心职责（照 RMB01 经验）：
# 1. 数据目录可写性检查（非 root 用户拿不到写权限会到用时才炸）
# 2. 拒绝明显错误的配置
set -eu

WORKERS="${GUNICORN_WORKERS:-1}"

# --- 检查 1：数据目录可写 ---
DATA_DIR="${RMB02_DATA_DIR:-/app/data}"
mkdir -p "$DATA_DIR" 2>/dev/null || true
if ! touch "$DATA_DIR/.write_test" 2>/dev/null; then
    echo "❌ 数据目录不可写：$DATA_DIR（当前用户 $(id -un)）" >&2
    echo "   容器以非 root 用户运行，挂载卷需要可写权限。" >&2
    exit 1
fi
rm -f "$DATA_DIR/.write_test"

# --- 检查 2：配了模型但没 key（提示，不阻塞）---
if [ -z "${RMB02_API_KEY:-}" ]; then
    echo "⚠️  未配置 RMB02_API_KEY —— 打分/生成会走本地规则降级。" >&2
    echo "   这不影响启动，但演示/生产环境应配置。" >&2
fi

echo "▶ 启动 RMB02 · worker=$WORKERS · data=$DATA_DIR"

exec gunicorn \
    --bind "0.0.0.0:${RMB02_PORT:-5102}" \
    --workers "$WORKERS" \
    --threads "${GUNICORN_THREADS:-4}" \
    --timeout "${GUNICORN_TIMEOUT:-60}" \
    --access-logfile - \
    --error-logfile - \
    --capture-output \
    "app:app"
