#!/usr/bin/env bash
# MiniMax-H3 FL2VA，官方 12 GB 显存档（SGLang Recipe A）：DiT/文本编码器/VAE 全部 layerwise offload，480p 级。
# 起服务后另开终端跑 ./request_480p.sh
set -euo pipefail
H3=/mnt/sdb1/soft/deploy-models/h3
export PYTORCH_CUDA_ALLOC_CONF=expandable_segments:True
export HF_HUB_OFFLINE=1 TOKENIZERS_PARALLELISM=false
exec "$H3/env/bin/sglang" serve \
  --model-path "$H3/models/MiniMax-H3" \
  --model-variant fl2va \
  --performance-mode memory \
  --layerwise-offload-components dit,text_encoder,vae \
  --host 127.0.0.1 --port 30010 "$@"
