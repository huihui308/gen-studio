#!/usr/bin/env bash
# MiniMax-H3 FL2VA，官方 "Recipe A"（12 GB VRAM + 32 GB host 档，SGLang cookbook）：
# DiT / 文本编码器 / VAE 全部 layerwise offload 流式进显存，仅 video_vae 的 36 个 decoder block 在解码阶段常驻。
# 本机：默认只用一张卡（CUDA_VISIBLE_DEVICES=1，另一张空闲，可用环境变量覆盖）。起服务后另开终端跑 ./request_480p.sh
set -euo pipefail
H3=/mnt/sdb1/soft/deploy-models/h3
export CUDA_VISIBLE_DEVICES="${CUDA_VISIBLE_DEVICES:-1}"
export PYTORCH_CUDA_ALLOC_CONF=expandable_segments:True
export TOKENIZERS_PARALLELISM=false
export SGLANG_USE_MODELSCOPE=false
exec "$H3/env/bin/sglang" serve \
  --model-path "$H3/models/MiniMax-H3" \
  --model-variant fl2va \
  --num-gpus 1 \
  --performance-mode memory \
  --layerwise-offload-components dit,text_encoder,vae \
  --layerwise-resident-layers video_vae=36 \
  --host 127.0.0.1 --port 30010 "$@"
