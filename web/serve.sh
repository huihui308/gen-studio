#!/usr/bin/env bash
# Gen Studio — 本机 Web 控制台启动脚本
#
#   默认访问: http://127.0.0.1:8090
#   绑定地址: 优先 GEN_STUDIO_HOST，其次 data/settings.json 的 bind_host
#             （页面「模型控制 → 绑定地址」保存），最后回退 127.0.0.1
#   可覆盖:   GEN_STUDIO_HOST / GEN_STUDIO_PORT / GEN_STUDIO_DATA_DIR
#
# 控制台通过 h3/serve_recipe_a.sh 与 qwen-image/serve.sh 启停模型，并按页面
# 所选显卡注入 CUDA_VISIBLE_DEVICES。监听地址只能在启动时决定，页面保存后需
# 重启本服务。
set -euo pipefail
cd "$(dirname "$0")"

PYTHON=".venv/bin/python"
if [[ ! -x "$PYTHON" ]]; then
  echo "错误：未找到 web/.venv。请先执行：" >&2
  echo "  cd web && uv venv .venv && uv pip install -r requirements.txt" >&2
  exit 1
fi

PORT="${GEN_STUDIO_PORT:-8090}"
export GEN_STUDIO_REPO_ROOT="${GEN_STUDIO_REPO_ROOT:-$(cd .. && pwd)}"
export GEN_STUDIO_DATA_DIR="${GEN_STUDIO_DATA_DIR:-$PWD/data}"
CONFIG="$GEN_STUDIO_DATA_DIR/settings.json"

# 1) environment variable, 2) page-saved settings.json, 3) loopback.
HOST="${GEN_STUDIO_HOST:-}"
if [[ -z "$HOST" && -f "$CONFIG" ]]; then
  HOST="$("$PYTHON" - "$CONFIG" <<'PY'
import json
import sys

try:
    with open(sys.argv[1], encoding="utf-8") as handle:
        host = json.load(handle).get("bind_host", "")
except (OSError, ValueError):
    host = ""
print(host if host in ("127.0.0.1", "0.0.0.0") else "127.0.0.1")
PY
)"
fi
if [[ -z "$HOST" ]]; then
  HOST="127.0.0.1"
fi
case "$HOST" in
  127.0.0.1|0.0.0.0) ;;
  *)
    echo "警告：不支持的绑定地址 '$HOST'，回退到 127.0.0.1" >&2
    HOST="127.0.0.1"
    ;;
esac
export GEN_STUDIO_HOST="$HOST"
export GEN_STUDIO_PORT="$PORT"

mkdir -p "$GEN_STUDIO_DATA_DIR/logs"
umask 077

echo "Gen Studio 控制台: http://${HOST}:${PORT}"
echo "数据目录: ${GEN_STUDIO_DATA_DIR}"
if [[ "$HOST" == "0.0.0.0" ]]; then
  echo "绑定所有网卡：局域网内可通过本机 IP:${PORT} 访问（注意防火墙放行）。"
fi

exec "$PYTHON" -m uvicorn app.main:app \
  --host "$HOST" \
  --port "$PORT" \
  --log-level "${GEN_STUDIO_LOG_LEVEL:-info}"
