#!/usr/bin/env bash
# ComfyUI backend for Qwen-Image-2.1-Turbo
#   GPU: 默认物理 0 号卡（RTX 3080 Ti 12GB，SM 8.6 Ampere）；
#        可由 CUDA_VISIBLE_DEVICES 覆盖（web 控制台按所选显卡注入）
#   端口 30020（30010 留给 h3 的 SGLang）；监听地址默认 127.0.0.1，可由
#   GEN_STUDIO_MODEL_HOST=0.0.0.0 覆盖（web 控制台的「模型访问地址」会注入）
set -euo pipefail
# 强约束（2026-11-12 David）：本机 h3 与 qwen-image 只能同时运行一个。
# 控制台启动路径已在 web/app/manager.py 做同样的互斥拒绝；这里是绕过控制台
# 手动启动时的兜底闸门——检测到 h3 后端（SGLang/MiniMax-H3）进程就直接退出。
if pgrep -f 'MiniMax-H3' >/dev/null 2>&1; then
  echo "互斥约束：MiniMax H3（SGLang）正在运行，本机同时只能跑一个生成模型；请先停止它再启动 qwen-image。" >&2
  exit 1
fi
cd "$(dirname "$0")"

export CUDA_VISIBLE_DEVICES="${CUDA_VISIBLE_DEVICES:-0}"
export PYTORCH_CUDA_ALLOC_CONF=expandable_segments:True

exec env/bin/python ComfyUI/main.py \
  --listen "${GEN_STUDIO_MODEL_HOST:-127.0.0.1}" \
  --port 30020 \
  --disable-auto-launch \
  --output-directory "$PWD/output" \
  --temp-directory "$PWD/temp" \
  "$@"
