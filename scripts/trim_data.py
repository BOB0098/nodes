#!/usr/bin/env python3
"""把 data/nodes.json 裁剪为前端展示数据 public/data/display.json。

前端运行时优先 fetch('data/display.json')（构建时由 public/ 拷入 dist/），
读不到时回退到 src/data/display.ts 内嵌的上一次采集值。
"""
import json
import os

SRC = "data/nodes.json"
DST = "public/data/display.json"
KEYS = ("updated", "ts", "raw", "errors", "groups")


def main() -> None:
    with open(SRC, encoding="utf-8") as f:
        data = json.load(f)
    trimmed = {k: data[k] for k in KEYS if k in data}
    os.makedirs(os.path.dirname(DST), exist_ok=True)
    with open(DST, "w", encoding="utf-8") as f:
        json.dump(trimmed, f, ensure_ascii=False, separators=(",", ":"))
    size = len(json.dumps(trimmed, ensure_ascii=False, separators=(",", ":")))
    print(f"OK: {DST} ({size} bytes)")


if __name__ == "__main__":
    main()
