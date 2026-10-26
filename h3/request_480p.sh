#!/usr/bin/env bash
# 发一条 864x480 / 5s / 20 步的 t2va（文字→带音轨视频），轮询到完成并下载 mp4。
set -euo pipefail
OUT=${OUT:-/mnt/sdb1/soft/deploy-models/h3/output}
PROMPT=${PROMPT:-"A red panda barista carefully pours latte art into a ceramic cup, morning sunlight, shallow depth of field."}
mkdir -p "$OUT"
id=$(curl -sS -X POST http://127.0.0.1:30010/v1/videos -H 'Content-Type: application/json' -d "{
  \"model\": \"/mnt/sdb1/soft/deploy-models/h3/models/MiniMax-H3\",
  \"prompt\": $(printf '%s' "$PROMPT" | python3 -c 'import json,sys;print(json.dumps(sys.stdin.read()))'),
  \"seconds\": 5, \"task\": \"t2va\", \"conditions\": [],
  \"target\": {\"short_edge\": 480, \"aspect_ratio\": \"16:9\", \"duration_seconds\": 5.0},
  \"num_outputs_per_prompt\": 1, \"num_inference_steps\": 20,
  \"flow_shift\": 12.0, \"audio_flow_shift\": 3.0, \"seed\": 1101
}" | python3 -c 'import json,sys;print(json.load(sys.stdin)["id"])')
echo "job: $id"
while true; do
  st=$(curl -sS "http://127.0.0.1:30010/v1/videos/$id" | python3 -c 'import json,sys;print(json.load(sys.stdin)["status"])')
  echo "  $st"; [ "$st" = completed ] && break; [ "$st" = failed ] && exit 1; sleep 10
done
curl -sS -L "http://127.0.0.1:30010/v1/videos/$id/content" -o "$OUT/h3_480p_$(date +%H%M%S).mp4"
ls -lh "$OUT" | tail -1
