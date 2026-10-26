#!/usr/bin/env bash
# Download Qwen-Image-2.1-Turbo ComfyUI weights from ModelScope into ComfyUI model dirs.
# Re-runnable: uses curl -C - and verifies byte counts against the recorded sizes.
set -uo pipefail
cd "${BASH_SOURCE[0]%/*}" || exit 1

REPO="Comfy-Org/Qwen-Image-2.1"
BASE="https://modelscope.cn/models/${REPO}/resolve/master"
CKPT="ComfyUI/models"
JOBS=3
LOG="logs"
mkdir -p "$LOG"

# <remote path> <size bytes> <local dir under ComfyUI/models>
MANIFEST=$(mktemp)
cat >"$MANIFEST" <<'EOF'
diffusion_models/qwen_image_2.1_turbo_int8_convrot.safetensors	7256787829	diffusion_models
text_encoders/qwen3vl_8b_w4a8.safetensors	6312105364	text_encoders
vae/qwen_image_2.1_vae_bf16.safetensors	675509688	vae
loras/qwen_image_2.1_turbo_lora_avg_rank_178_bf16.safetensors	913314512	loras
EOF

fetch() {  # $1 remote path, $2 size, $3 dest dir
  local rp="$1" size="$2" dir="$3"
  local name dest
  name=$(basename "$rp")
  dest="$CKPT/$dir/$name"
  mkdir -p "$CKPT/$dir"
  local have=0
  [ -f "$dest" ] && have=$(stat -c%s "$dest")
  if [ "$have" = "$size" ]; then
    echo "[skip] $name already complete ($size bytes)"
    return 0
  fi
  echo "[get ] $name -> $dest (need $size, have $have)"
  for attempt in 1 2 3 4 5; do
    curl -sSL -C - --retry 8 --retry-delay 3 --retry-all-errors -o "$dest" "$BASE/$rp"
    local now
    now=$(stat -c%s "$dest" 2>/dev/null || echo 0)
    if [ "$now" = "$size" ]; then
      echo "[ ok ] $name $now bytes"
      return 0
    fi
    echo "[retry] $name attempt $attempt: $now/$size"
  done
  echo "[FAIL] $name size mismatch"
  return 1
}

export -f fetch
export BASE CKPT

awk -F'\t' '{print $1"\t"$2"\t"$3}' "$MANIFEST" |
  xargs -P "$JOBS" -I{} bash -c 'IFS=$'"'"'\t'"'"' read -r a b c <<<"{}"; fetch "$a" "$b" "$c"'

echo "=== summary ==="
fail=0
while IFS=$'\t' read -r rp size dir; do
  name=$(basename "$rp"); dest="$CKPT/$dir/$name"
  now=$(stat -c%s "$dest" 2>/dev/null || echo 0)
  if [ "$now" = "$size" ]; then echo "OK   $name $now"; else echo "BAD  $name $now/$size"; fail=1; fi
done <"$MANIFEST"
rm -f "$MANIFEST"
exit $fail
