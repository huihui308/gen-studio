#!/usr/bin/env bash
# 从 ModelScope 拉取 MiniMax-H3 的 FL2VA（图/文→视频+音频）权重，可断点续传。
# 用法: ./download_fl2va.sh            # 全量 ~144 GB
#       PATTERN='config.json' ./download_fl2va.sh   # 只下配置文件（自检）
set -uo pipefail
DST="${DST:-/mnt/sdb1/soft/deploy-models/h3/models/MiniMax-H3}"
PREFIX="${PREFIX:-FL2VA/}"
PATTERN="${PATTERN:-.}"
JOBS="${JOBS:-6}"
BASE="https://modelscope.cn/models/MiniMax/MiniMax-H3/resolve/master"
LIST=/tmp/h3_fl2va_files.txt
mkdir -p "$DST"

if [ ! -s "$LIST" ]; then
  curl -s "https://modelscope.cn/api/v1/models/MiniMax/MiniMax-H3/repo/files?Recursive=true&PageSize=1000" \
   | python3 -c "
import json,sys
d=json.load(sys.stdin)
for f in d['Data']['Files']:
    if f.get('Type')=='blob' and f['Path'].startswith('$PREFIX'): print(f['Path'], f.get('Size',0))
" > "$LIST"
fi

grep -E "$PATTERN" "$LIST" | awk '{printf "%s %s\n",$1,$2}' | xargs -P "$JOBS" -n 2 bash -c '
  f="$0"; size="$1"; dst="'"$DST"'/$f"
  mkdir -p "$(dirname "$dst")"
  if [ -f "$dst" ] && [ "$(stat -c%s "$dst")" = "$size" ]; then echo "skip $f"; exit 0; fi
  echo "get  $f ($(( size/1048576 )) MB)"
  curl -sSL -C - --retry 5 --retry-delay 3 -o "$dst" "'"$BASE"'/$f" || echo "FAIL $f"
'
echo "== 完成状态 =="
python3 - "$DST" "$LIST" <<'PY'
import os,sys
dst, lst = sys.argv[1], sys.argv[2]
tot=got=0; bad=[]
for line in open(lst):
    p, s = line.split(); s=int(s); tot+=s
    fp=os.path.join(dst,p)
    if os.path.exists(fp): got+=os.path.getsize(fp)
    if not os.path.exists(fp) or os.path.getsize(fp)!=s: bad.append(p)
print(f"{got/1e9:.1f}/{tot/1e9:.1f} GB")
print("missing/incomplete:", len(bad)); [print("  ",x) for x in bad[:10]]
PY
