#!/usr/bin/env bash
# 下载 sglang 需要的“根布局” FL2VA 权重（transformer/text_encoder/vae/audio_vae/tokenizer/processor/scheduler + 根 json），
# 排除 Ref2VA / FL2VA 分区副本 / transformer_ref / assets / docs。可断点续传。
set -uo pipefail
DST="${DST:-/mnt/sdb1/soft/deploy-models/h3/models/MiniMax-H3}"
JOBS="${JOBS:-8}"
BASE="https://modelscope.cn/models/MiniMax/MiniMax-H3/resolve/master"
LIST="${LIST:-/tmp/h3_root_files.txt}"
mkdir -p "$DST"
echo "list: $LIST ($(wc -l < "$LIST") files, $(awk '{s+=$2} END{printf "%.1f", s/2**30}' "$LIST") GiB)"
awk '{print $1, $2}' "$LIST" | xargs -P "$JOBS" -n 2 bash -c '
  f="$0"; size="$1"; dst="'"$DST"'/$f"
  mkdir -p "$(dirname "$dst")"
  if [ -f "$dst" ] && [ "$(stat -c%s "$dst")" = "$size" ]; then echo "skip $f"; exit 0; fi
  echo "get  $f ($(( size/1048576 )) MB)"
  curl -sSL -C - --retry 8 --retry-delay 3 --retry-all-errors -o "$dst" "'"$BASE"'/$f" || echo "FAIL $f"
'
echo "== 完成状态 =="
python3 - "$DST" "$LIST" <<'PY'
import os,sys
dst,lst=sys.argv[1],sys.argv[2]; tot=got=0; bad=[]
for line in open(lst):
    p,s=line.split(); s=int(s); tot+=s
    fp=os.path.join(dst,p)
    if os.path.exists(fp): got+=os.path.getsize(fp)
    if not os.path.exists(fp) or os.path.getsize(fp)!=s: bad.append(p)
print(f"{got/1e9:.1f}/{tot/1e9:.1f} GB  missing/incomplete={len(bad)}")
for x in bad[:10]: print("  ",x)
PY
