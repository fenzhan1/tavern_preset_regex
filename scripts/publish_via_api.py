"""发布新版本：本地提交 + 通过 GitHub API 推送源码。

在 ``github.com`` 的 git 协议被网络阻断时，``git push`` 用不了；本脚本用
Git Data API 把工作区内容打成新提交推到远端，并把版本标签指向它，随后由
``mpdt market package-update --skip-push`` 完成 Release 与市场提交。

用法：

    python scripts/publish_via_api.py --tag v2.4.0 --message "..." --dry-run
    python scripts/publish_via_api.py --tag v2.4.0 --message "..."
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from push_via_api import (
    BRANCH,
    OWNER,
    PLUGIN_ROOT,
    REPO,
    SKIP_PREFIXES,
    SKIP_SUFFIXES,
    api,
    load_token,
)

# 仓库里纳入版本管理的目录/文件（dist、缓存等构建产物排除）
SKIP_DIRS = ("dist", "__pycache__", ".ruff_cache", ".pytest_cache", ".git")


def run_git(*args: str, input_bytes: bytes | None = None) -> str:
    result = subprocess.run(
        ["git", "-C", str(PLUGIN_ROOT), "-c", "core.autocrlf=false", *args],
        capture_output=True,
        input=input_bytes,
        check=False,
    )
    if result.returncode != 0:
        raise SystemExit(
            f"git {' '.join(args)} 失败: "
            f"{result.stderr.decode('utf-8', 'replace').strip()}"
        )
    return result.stdout.decode("utf-8", "replace").strip()


def worktree_files() -> list[str]:
    files: list[str] = []
    for path in sorted(PLUGIN_ROOT.rglob("*")):
        if not path.is_file():
            continue
        rel = path.relative_to(PLUGIN_ROOT)
        parts = rel.parts
        if any(part in SKIP_DIRS for part in parts):
            continue
        normalized = rel.as_posix()
        if normalized.startswith(SKIP_PREFIXES) or normalized.endswith(SKIP_SUFFIXES):
            continue
        files.append(normalized)
    return files


def write_blob(path: str) -> str:
    data = (PLUGIN_ROOT / path).read_bytes().replace(b"\r\n", b"\n")
    return run_git("hash-object", "-w", "--stdin", input_bytes=data)


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
    payload = ("\n".join(lines) + "\n").encode("utf-8")
    return run_git("mktree", input_bytes=payload)


def remote_commit_sha(token: str, ref_path: str) -> str:
    ref = api("GET", f"/repos/{OWNER}/{REPO}/git/ref/{ref_path}", token)
    return ref["object"]["sha"]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--tag", required=True)
    parser.add_argument("--message", required=True)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    token = load_token()
    parent = remote_commit_sha(token, f"heads/{BRANCH}")
    print(f"远端 {BRANCH} 当前提交：{parent[:10]}")

    files = worktree_files()
    print(f"工作区纳入版本管理的文件：{len(files)} 个")

    if args.dry_run:
        for path in files:
            print(f"  - {path}")
        print("--dry-run：未写入")
        return 0

    # 1. 本地：把工作区打成 tree（父提交用远端提交，本地可能没有该对象，
    #    因此提交对象只在远端创建，避免本地 commit-tree 找不到父提交）
    shas = {path: write_blob(path) for path in files}
    tree_sha = write_tree(shas, files)
    print(f"本地 tree：{tree_sha[:10]}")

    # 2. 远端：用同样的 tree 与父提交创建提交
    remote_entries: list[dict[str, str]] = []
    for path in files:
        blob = api(
            "POST",
            f"/repos/{OWNER}/{REPO}/git/blobs",
            token,
            {
                "content": (PLUGIN_ROOT / path)
                .read_bytes()
                .replace(b"\r\n", b"\n")
                .decode("utf-8"),
                "encoding": "utf-8",
            },
        )
        remote_entries.append(
            {"path": path, "mode": "100644", "type": "blob", "sha": blob["sha"]}
        )
    remote_tree = api(
        "POST",
        f"/repos/{OWNER}/{REPO}/git/trees",
        token,
        {"tree": remote_entries},
    )
    print(f"远端 tree：{remote_tree['sha'][:10]}")

    remote_commit = api(
        "POST",
        f"/repos/{OWNER}/{REPO}/git/commits",
        token,
        {
            "message": args.message,
            "tree": remote_tree["sha"],
            "parents": [parent],
        },
    )
    commit_sha = remote_commit["sha"]
    print(f"远端提交：{commit_sha[:10]}")

    # 3. 更新分支与标签
    api(
        "PATCH",
        f"/repos/{OWNER}/{REPO}/git/refs/heads/{BRANCH}",
        token,
        {"sha": commit_sha, "force": True},
    )
    print(f"远端 {BRANCH} 已更新")

    try:
        existing = api("GET", f"/repos/{OWNER}/{REPO}/git/ref/tags/{args.tag}", token)
    except SystemExit:
        existing = None
    if existing:
        api(
            "PATCH",
            f"/repos/{OWNER}/{REPO}/git/refs/tags/{args.tag}",
            token,
            {"sha": commit_sha, "force": True},
        )
        print(f"标签 {args.tag} 已移动")
    else:
        api(
            "POST",
            f"/repos/{OWNER}/{REPO}/git/refs",
            token,
            {"ref": f"refs/tags/{args.tag}", "sha": commit_sha},
        )
        print(f"标签 {args.tag} 已创建")

    # 4. 本地有父提交时才对齐本地 refs（否则本地保留旧历史，等能 fetch 时再对齐）
    try:
        run_git("cat-file", "-t", parent)
        has_parent = True
    except SystemExit:
        has_parent = False
    if has_parent:
        run_git("update-ref", f"refs/heads/{BRANCH}", commit_sha)
        run_git("update-ref", f"refs/tags/{args.tag}", commit_sha)
        run_git("reset", "--mixed", commit_sha)
        print("本地 refs 已对齐")
    else:
        print(f"本地缺少父提交 {parent[:10]}，跳过本地 refs 对齐（内容已推到远端）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
