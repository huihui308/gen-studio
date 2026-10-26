#!/usr/bin/env bash
# Gen Studio — 本机 Web 控制台启动脚本
#
#   访问地址: http://127.0.0.1:8090
#   可覆盖:   GEN_STUDIO_HOST / GEN_STUDIO_PORT / GEN_STUDIO_DATA_DIR
#
# 控制台只监听回环地址；它通过 h3/serve_recipe_a.sh 与
# qwen-image/serve.sh 启停模型，并按页面所选显卡注入 CUDA_VISIBLE_DEVICES。
set -euo pipefail
cd "$(dirname "$0")"

PYTHON=".venv/bin/python"
if [[ ! -x "$PYTHON" ]]; then
  echo "错误：未找到 web/.venv。请先执行：" >&2
  echo "  cd web && uv venv .venv && uv pip install -r requirements.txt" >&2
  exit 1
fi

HOST="${GEN_STUDIO_HOST:-127.0.0.1}"
PORT="${GEN_STUDIO_PORT:-8090}"
export GEN_STUDIO_REPO_ROOT="${GEN_STUDIO_REPO_ROOT:-$(cd .. && pwd)}"
export GEN_STUDIO_DATA_DIR="${GEN_STUDIO_DATA_DIR:-$PWD/data}"

mkdir -p "$GEN_STUDIO_DATA_DIR/logs"
umask 077

echo "Gen Studio 控制台: http://${HOST}:${PORT}"
echo "数据目录: ${GEN_STUDIO_DATA_DIR}"

exec "$PYTHON" -m uvicorn app.main:app \
  --host "$HOST" \
  --port "$PORT" \
  --log-level "${GEN_STUDIO_LOG_LEVEL:-info}"
