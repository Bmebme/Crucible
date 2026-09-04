#!/usr/bin/env python3
"""MinerU 模型补全下载器 (部署必读, 见 docs/deploy.md)。

背景 (实测 2026-09-04): HF 官方仓库 opendatalab/PDF-Extract-Kit-1.0 经
hf-mirror 下载时, Layout/MFR/OCR 之外的全部目录 (TabRec/TabCls/MFD/OriCls/
ReadingOrder) 的 LFS 文件缺失; ModelScope 同名仓库有完整文件, 但目录级
下载 API 有缺陷, 必须逐文件递归下载。本脚本按此实测路径补全。

用法 (在 docreader 容器内):
  python3 /tmp/fetch_mineru_models.py [--snapshot <快照目录>]
默认写入 HF 缓存当前 PDF-Extract-Kit-1.0 快照的 models/ 下。
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import urllib.request
from pathlib import Path

BASE_API = "https://modelscope.cn/api/v1/models/OpenDataLab/PDF-Extract-Kit-1.0/repo/files"
RESOLVE = "https://modelscope.cn/models/OpenDataLab/PDF-Extract-Kit-1.0/resolve/master/"
HF_SNAP = (
    "/root/.cache/huggingface/hub/models--opendatalab--PDF-Extract-Kit-1.0/snapshots"
)
MISSING_DIRS = ("TabCls", "MFD", "OriCls", "ReadingOrder", "TabRec")


def list_files(root: str) -> list[dict]:
    url = f"{BASE_API}?Revision=master&Root={root}"
    data = json.load(urllib.request.urlopen(url, timeout=60))
    return (data.get("Data") or {}).get("Files") or []


def walk(root: str, snap: Path) -> None:
    for f in list_files(root):
        path = f.get("Path")
        if not path:
            continue
        if f.get("Type") == "tree":
            walk(path, snap)
        else:
            rel = path[len("models/"):]
            dest = snap / "models" / rel
            dest.parent.mkdir(parents=True, exist_ok=True)
            size = int(f.get("Size") or 0)
            if dest.exists() and dest.stat().st_size == size:
                print("skip", rel)
                continue
            subprocess.run(
                ["curl", "-sL", "--max-time", "900", "-o", str(dest), RESOLVE + path],
                check=True,
            )
            print("got", rel, dest.stat().st_size)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--snapshot", default="")
    args = parser.parse_args()

    if args.snapshot:
        snap = Path(args.snapshot)
    else:
        hub = Path(HF_SNAP)
        cands = sorted(hub.glob("models--opendatalab--PDF-Extract-Kit-1.0/snapshots/*"))
        if not cands:
            raise SystemExit("未找到 PDF-Extract-Kit-1.0 快照目录, 先跑一次 mineru 触发下载")
        snap = cands[-1]
    print("snapshot:", snap)
    for d in MISSING_DIRS:
        print("== walk", d)
        walk(f"models/{d}", snap)
    print("ALL DONE")


if __name__ == "__main__":
    main()
