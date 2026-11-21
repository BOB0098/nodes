#!/usr/bin/env bash
# 每日采集脚本（本地 cron 用）
cd "$(dirname "$0")" || exit 1
python3 collector.py >> logs.txt 2>&1
echo "---- $(date '+%Y-%m-%d %H:%M') 完成 ----" >> logs.txt
