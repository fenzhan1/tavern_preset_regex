"""通过 GitHub REST API 删除指定 Release 与标签（用于重发同版本）。

用法：

    python scripts/reset_release_via_api.py v2.3.1 --dry-run
    python scripts/reset_release_via_api.py v2.3.1

Token 从 ``~/.mpdt/config.toml`` 的 ``[github] token`` 读取。
"""

# ruff: noqa: I001 - 需要先补齐 sys.path 才能导入插件模块

from __future__ import annotations

import argparse
import json
import sys
import urllib.error
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from push_via_api import API, OWNER, REPO, api, load_token  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("tag")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    token = load_token()
    tag = args.tag

    try:
        release = api("GET", f"/repos/{OWNER}/{REPO}/releases/tags/{tag}", token)
        print(f"找到 Release: id={release['id']} tag={release['tag_name']}")
        if not args.dry_run:
            api("DELETE", f"/repos/{OWNER}/{REPO}/releases/{release['id']}", token)
            print("Release 已删除")
    except SystemExit:
        print(f"Release {tag} 不存在")

    try:
        ref = api("GET", f"/repos/{OWNER}/{REPO}/git/ref/tags/{tag}", token)
        print(f"找到标签: {tag} -> {ref['object']['sha'][:10]}")
        if not args.dry_run:
            api("DELETE", f"/repos/{OWNER}/{REPO}/git/refs/tags/{tag}", token)
            print("标签已删除")
    except SystemExit:
        print(f"标签 {tag} 不存在")

    if args.dry_run:
        print("\n--dry-run：未执行任何删除")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
