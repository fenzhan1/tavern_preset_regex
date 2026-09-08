"""通过 GitHub REST API 重新创建 Release 并上传资产。

用于 ``mpdt market package-update`` 删除了旧 Release 后需要重建的场景
（版本已在市场中存在，命令会拒绝重跑）。

用法：

    python scripts/recreate_release_via_api.py v2.4.0
"""

from __future__ import annotations

import argparse
import json
import sys
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from push_via_api import OWNER, PLUGIN_ROOT, REPO, api, load_token

UPLOADS = "https://uploads.github.com"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("tag")
    parser.add_argument("--title", default="")
    parser.add_argument("--body", default="")
    args = parser.parse_args()

    token = load_token()
    tag = args.tag
    package = PLUGIN_ROOT / "dist" / f"tavern_preset_regex-{tag.lstrip('v')}.mfp"
    if not package.is_file():
        raise SystemExit(f"找不到插件包: {package}")

    ref = api("GET", f"/repos/{OWNER}/{REPO}/git/ref/tags/{tag}", token)
    print(f"标签 {tag} -> {ref['object']['sha'][:10]}")

    try:
        existing = api("GET", f"/repos/{OWNER}/{REPO}/releases/tags/{tag}", token)
        print(f"Release 已存在: {existing['html_url']}")
        return 0
    except SystemExit:
        pass

    import hashlib

    digest = hashlib.sha256(package.read_bytes()).hexdigest()
    body = args.body or f"`{package.name}`\n\nSHA256: `{digest}`"
    release = api(
        "POST",
        f"/repos/{OWNER}/{REPO}/releases",
        token,
        {
            "tag_name": tag,
            "name": args.title or f"tavern_preset_regex {tag}",
            "body": body,
            "draft": False,
            "prerelease": False,
        },
    )
    print(f"Release 已创建: {release['html_url']}")

    data = package.read_bytes()
    request = urllib.request.Request(
        f"{UPLOADS}/repos/{OWNER}/{REPO}/releases/{release['id']}/assets?name={package.name}",
        data=data,
        method="POST",
        headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
            "Content-Type": "application/octet-stream",
            "User-Agent": "tavern-preset-regex-release",
        },
    )
    with urllib.request.urlopen(request, timeout=120) as response:
        asset = json.loads(response.read().decode("utf-8"))
    print(f"资产已上传: {asset['name']} {asset['size']} 字节")
    print(f"SHA256: {digest}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
