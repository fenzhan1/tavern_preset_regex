"""把本地 git 对象与 refs 对齐到通过 GitHub API 推送的提交。

用途：``git push`` 被网络阻断时，源码只能走 REST API 推送，本地仓库会落后于远端。
本脚本按远端提交的 tree 在本地重建 blob/tree 对象，再按远端的 author/committer/时间
重建提交对象，让本地 refs 与远端一致，后续提交才有正确的父提交。

用法：

    python scripts/sync_git_from_remote.py --tag v2.3.1
    python scripts/sync_git_from_remote.py --tag v2.3.1 --dry-run
"""

# ruff: noqa: I001 - 需要先补齐 sys.path 才能导入插件模块

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from push_via_api import (  # noqa: E402
    BRANCH,
    OWNER,
    PLUGIN_ROOT,
    REPO,
    api,
    load_token,
)


def run_git(
    *args: str,
    input_text: str | None = None,
    env: dict[str, str] | None = None,
) -> str:
    import os

    merged = dict(os.environ)
    if env:
        merged.update(env)
    # 注意：必须以字节形式传 stdin，否则 Windows 文本模式会把 \n 翻成 \r\n，
    # 污染 hash-object / mktree 的输入。
    result = subprocess.run(
        ["git", "-C", str(PLUGIN_ROOT), *args],
        capture_output=True,
        input=input_text.encode("utf-8") if input_text is not None else None,
        env=merged,
    )
    if result.returncode != 0:
        raise SystemExit(
            f"git {' '.join(args)} 失败: {result.stderr.decode('utf-8', 'replace').strip()}"
        )
    return result.stdout.decode("utf-8", "replace").strip()


def remote_blobs(token: str, tree_sha: str) -> dict[str, str]:
    tree = api(
        "GET",
        f"/repos/{OWNER}/{REPO}/git/trees/{tree_sha}?recursive=1",
        token,
    )
    return {
        item["path"]: item["sha"] for item in tree["tree"] if item["type"] == "blob"
    }


def write_blob(path: str) -> str:
    """按 LF 归一化写入 blob（对象库存 LF 版本）。"""
    data = (PLUGIN_ROOT / path).read_text(encoding="utf-8").replace("\r\n", "\n")
    return run_git(
        "-c",
        "core.autocrlf=false",
        "hash-object",
        "-w",
        "--stdin",
        input_text=data,
    )


def write_tree(shas: dict[str, str], paths: list[str], prefix: str = "") -> str:
    groups: dict[str, list[str] | None] = {}
    for path in paths:
        rest = path[len(prefix) :] if prefix else path
        if "/" in rest:
            groups.setdefault(rest.split("/", 1)[0], [])
        else:
            groups[rest] = None

    lines: list[str] = []
    for name, children in groups.items():
        full = f"{prefix}{name}"
        if children is None:
            lines.append(f"100644 blob {shas[full]}\t{name}")
        else:
            sub_paths = [p for p in paths if p.startswith(f"{full}/")]
            sub_sha = write_tree(shas, sub_paths, prefix=f"{full}/")
            lines.append(f"040000 tree {sub_sha}\t{name}")
    return run_git("mktree", input_text="\n".join(lines) + "\n")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--tag", default="v2.3.1")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    token = load_token()
    ref = api("GET", f"/repos/{OWNER}/{REPO}/git/ref/tags/{args.tag}", token)
    remote_sha = ref["object"]["sha"]
    commit = api("GET", f"/repos/{OWNER}/{REPO}/git/commits/{remote_sha}", token)
    remote_tree = commit["tree"]["sha"]
    print(f"远端提交 {remote_sha[:10]} tree={remote_tree[:10]}")
    print(f"远端父提交：{[p['sha'][:10] for p in commit.get('parents', [])]}")

    # 远端父提交必须已存在于本地，否则无法在本地重建这条历史。
    parents: list[str] = []
    for parent in commit.get("parents", []):
        parent_sha = parent["sha"]
        try:
            kind = run_git("cat-file", "-t", parent_sha)
        except SystemExit:
            print(f"本地缺少父提交 {parent_sha[:10]}，无法在本地重建该历史")
            return 1
        print(f"本地父提交 {parent_sha[:10]} 类型 {kind}")
        parents.append(parent_sha)

    if args.dry_run:
        print("--dry-run：未写入")
        return 0

    blobs = remote_blobs(token, remote_tree)
    print(f"远端 tree 文件数：{len(blobs)}")

    shas = {path: write_blob(path) for path in blobs}
    mismatched = [path for path, sha in shas.items() if sha != blobs[path]]
    print(f"blob 一致：{len(blobs) - len(mismatched)}/{len(blobs)}")
    for path in mismatched:
        print(f"  差异: {path}")

    local_tree = write_tree(shas, list(blobs))
    print(f"本地 tree -> {local_tree[:10]}（远端 {remote_tree[:10]}）")

    author = commit["author"]
    parent_args = [item for sha in parents for item in ("-p", sha)]
    local_sha = run_git(
        "-c",
        f"user.name={author['name']}",
        "-c",
        f"user.email={author['email']}",
        "commit-tree",
        local_tree,
        *parent_args,
        input_text=commit["message"],
        env={
            "GIT_AUTHOR_DATE": author["date"],
            "GIT_COMMITTER_DATE": commit["committer"]["date"],
        },
    )
    print(f"本地提交 -> {local_sha[:10]}（远端 {remote_sha[:10]}）")

    run_git("update-ref", f"refs/heads/{BRANCH}", local_sha)
    run_git("update-ref", f"refs/tags/{args.tag}", local_sha)
    run_git("reset", "--mixed", local_sha)
    print("已对齐本地 refs 与工作区")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
