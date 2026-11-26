#!/usr/bin/env bash
# 每日采集脚本（本地 cron 用）
cd "$(dirname "$0")" || exit 1
if python3 collector.py >> logs.txt 2>&1; then
  echo "---- $(date '+%Y-%m-%d %H:%M') 完成 ----" >> logs.txt
else
  rc=$?
  echo "---- $(date '+%Y-%m-%d %H:%M') 失败（rc=$rc）----" >> logs.txt
  exit "$rc"
fi
