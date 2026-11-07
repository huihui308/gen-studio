#!/usr/bin/env bash
# 串行生成「生肖母女 · 15s」三条 5s 片段（480×864 / 9:16 / t2va），
# 一条跑完再发下一条（单卡串行，避免 OOM），自动下载到 h3/output/zodiac-mother-daughter/。
#
# 用法：cd h3/payloads/zodiac-mother-daughter && ./run_all.sh
# 预计 480p / 20 步约 7 分钟/条，三条串行约 20–25 分钟。
set -euo pipefail
cd "$(dirname "$0")"

BASE=${H3_BASE:-http://127.0.0.1:30010}
OUT=${OUT:-/mnt/sdb1/soft/deploy-models/h3/output/zodiac-mother-daughter}
mkdir -p "$OUT"

for n in 1 2 3; do
  f=$(ls "clip${n}-"*-480p-9x16.json)
  echo "==> [$n/3] submit $f"
  id=$(curl -sS -X POST "$BASE/v1/videos" \
        -H 'Content-Type: application/json' -d @"$f" \
        | python3 -c 'import json,sys; d=json.load(sys.stdin); print(d.get("id") or d)')
  echo "    job: $id"

  while :; do
    st=$(curl -sS "$BASE/v1/videos/$id" \
          | python3 -c 'import json,sys; print(json.load(sys.stdin)["status"])')
    echo "    $st"
    case "$st" in
      completed) break ;;
      failed|cancelled|error) echo "job $id failed, stop"; exit 1 ;;
    esac
    sleep 10
  done

  out="$OUT/clip${n}.mp4"
  curl -sS -L "$BASE/v1/videos/$id/content" -o "$out"
  echo "    saved -> $out"
done

echo "done. clips in $OUT"
echo "next: 每段裁到 5.000s 并 loudnorm 后拼接（命令见 h3/inputs/zodiac-mother-daughter/README.md）"
