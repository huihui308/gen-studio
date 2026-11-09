#!/usr/bin/env bash
# Qwen-Image-2.1-Turbo HTTP 生图服务（后台常驻薄封装）
#   start/stop/status 管理 gen_api.py；日志落 logs/gen_api.log
#   依赖：ComfyUI 已由 ./serve.sh 起在 127.0.0.1:30020
#   默认对外监听 0.0.0.0:30021（无鉴权，靠内网/防火墙隔离）；设 GEN_STUDIO_IMAGE_API_HOST=127.0.0.1 可收回本机
set -euo pipefail
cd "$(dirname "$0")"

PID_FILE=logs/gen_api.pid
LOG_FILE=logs/gen_api.log
PORT="${GEN_STUDIO_IMAGE_API_PORT:-30021}"
HOST="${GEN_STUDIO_IMAGE_API_HOST:-0.0.0.0}"   # 局域网其他设备/程序调用需要对外监听；只想本机用时设 127.0.0.1

is_running() {
  [[ -f "$PID_FILE" ]] && kill -0 "$(cat "$PID_FILE")" 2>/dev/null
}

case "${1:-start}" in
  start)
    if is_running; then
      echo "[gen_api] 已在运行 pid=$(cat "$PID_FILE")  http://$HOST:$PORT"
      exit 0
    fi
    nohup env/bin/python gen_api.py --host "$HOST" --port "$PORT" >>"$LOG_FILE" 2>&1 &
    echo $! >"$PID_FILE"
    sleep 1
    if is_running; then
      echo "[gen_api] 已启动 pid=$(cat "$PID_FILE")  http://$HOST:$PORT  (日志 $LOG_FILE)"
    else
      echo "[gen_api] 启动失败，末尾日志："; tail -20 "$LOG_FILE"; exit 1
    fi
    ;;
  stop)
    if is_running; then
      kill "$(cat "$PID_FILE")" && echo "[gen_api] 已停止 pid=$(cat "$PID_FILE")"
      rm -f "$PID_FILE"
    else
      echo "[gen_api] 未运行"
    fi
    ;;
  status)
    if is_running; then
      echo "[gen_api] running pid=$(cat "$PID_FILE")  http://$HOST:$PORT"
      curl -s --max-time 5 "http://127.0.0.1:$PORT/health" || true; echo
    else
      echo "[gen_api] stopped"
    fi
    ;;
  *)
    echo "用法: $0 {start|stop|status}"; exit 2
    ;;
esac
