#!/bin/bash
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# gpu_temp_check.sh
# Periodic temperature/fan/power logger for both GPUs backing the dual-GPU
# paper_proc workers (see docs/sessions/1787453228_pin-model-quadro-p4000/).
# Run on a schedule via cron (every 5 min — see this repo's
# docs/sessions/1787453721_gpu-temp-monitoring/ for setup).
#
# Always appends a CSV row per GPU. Additionally `wall`-broadcasts once when
# a GPU's temp crosses WARN_C, not on every sample while it stays hot, so a
# sustained hot streak doesn't spam every logged-in terminal every 5 minutes.
# The alert re-arms once that GPU drops back below WARN_C.
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

LOG="/home/ricky/programs/paper_proc/logs/gpu_temp.csv"
STATE_DIR="/tmp/gpu_temp_check_state"
WARN_C=85

mkdir -p "$STATE_DIR"
if [ ! -f "$LOG" ]; then
    echo "timestamp,gpu_index,name,temp_c,fan_pct,power_w,util_pct" > "$LOG"
fi

nvidia-smi --query-gpu=index,name,temperature.gpu,fan.speed,power.draw,utilization.gpu \
    --format=csv,noheader,nounits | while IFS=',' read -r idx name temp fan power util; do
    idx=$(echo "$idx" | xargs)
    name=$(echo "$name" | xargs)
    temp=$(echo "$temp" | xargs)
    fan=$(echo "$fan" | xargs)
    power=$(echo "$power" | xargs)
    util=$(echo "$util" | xargs)
    ts=$(date '+%Y-%m-%d %H:%M:%S')

    echo "${ts},${idx},${name},${temp},${fan},${power},${util}" >> "$LOG"

    state_file="${STATE_DIR}/gpu${idx}_warned"
    if [ "${temp:-0}" -ge "$WARN_C" ] 2>/dev/null; then
        if [ ! -f "$state_file" ]; then
            wall "⚠️  GPU${idx} (${name}) at ${temp}C — at/above ${WARN_C}C warn threshold. Check: nvidia-smi" 2>/dev/null
            touch "$state_file"
        fi
    else
        rm -f "$state_file"
    fi
done
