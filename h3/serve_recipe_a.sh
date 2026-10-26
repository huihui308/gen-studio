#!/usr/bin/env bash
# MiniMax-H3 FL2VA，官方 "Recipe A"（12 GB VRAM + 32 GB host 档，SGLang cookbook）：
# DiT / 文本编码器 / VAE 全部 layerwise offload 流式进显存，仅 video_vae 的 36 个 decoder block 在解码阶段常驻。
# 本机：默认只用一张卡（CUDA_VISIBLE_DEVICES=1，另一张空闲，可用环境变量覆盖）。起服务后另开终端跑 ./request_480p.sh
#
# 两个必须的 CUDA/JIT 修补（否则第一条请求 build JIT kernel 就失败）：
#   1. sglang 用 $CUDA_HOME/bin/nvcc 编译 kernel，且带 `-std=c++20`：系统 /usr/local/cuda 若指向 11.8 会报
#      "Value 'c++20' is not defined for option 'std'"；这里指向 pip 装进 env312 的 cu13 toolkit（nvcc 13.4，与 torch cu130 对齐）。
#   2. 链接命令写死 `-L$CUDA_HOME/lib64 -lcudart`，而 pip 包只有 lib/ 且没有无版本号的 libcudart.so：
#      这里补 lib64 -> lib、libcudart.so -> libcudart.so.13 两个软链（在 env312 内部，只需一次）。
#   3. 还需要 ninja 在 PATH 上（env312/bin/ninja，装 sglang 时已带进来）。
set -euo pipefail
H3=${H3:-/mnt/sdb1/soft/deploy-models/h3}
CU13="$H3/env312/lib/python3.12/site-packages/nvidia/cu13"

export CUDA_VISIBLE_DEVICES="${CUDA_VISIBLE_DEVICES:-1}"
export PYTORCH_CUDA_ALLOC_CONF=expandable_segments:True
export TOKENIZERS_PARALLELISM=false
export SGLANG_USE_MODELSCOPE=false

export CUDA_HOME="$CU13"
export PATH="$CU13/bin:$H3/env312/bin:$PATH"
[ -e "$CU13/lib64" ] || ln -sfn lib "$CU13/lib64"
[ -e "$CU13/lib/libcudart.so" ] || ln -sfn libcudart.so.13 "$CU13/lib/libcudart.so"
command -v ninja >/dev/null || { echo "缺少 ninja：先跑 uv pip install --python $H3/env312/bin/python ninja" >&2; exit 1; }

exec "$H3/env312/bin/sglang" serve \
  --model-path "$H3/models/MiniMax-H3" \
  --model-variant fl2va \
  --num-gpus 1 \
  --performance-mode memory \
  --layerwise-offload-components dit,text_encoder,vae \
  --layerwise-resident-layers video_vae=36 \
  --host 127.0.0.1 --port 30010 "$@"
