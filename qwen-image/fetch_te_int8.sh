#!/usr/bin/env bash
set -u
D=ComfyUI/models/text_encoders
F=qwen3vl_8b_int8_convrot.safetensors
NEED=9350798360
URL="https://modelscope.cn/models/Comfy-Org/Qwen-Image-2.1/resolve/master/text_encoders/$F"
echo "[get ] $F need $NEED"
curl -sSL -C - --retry 8 --retry-delay 3 --retry-all-errors -o "$D/$F" "$URL"
have=$(stat -c%s "$D/$F" 2>/dev/null || echo 0)
if [ "$have" = "$NEED" ]; then echo "[ ok ] $F $have"; else echo "[BAD ] $F $have != $NEED"; fi
