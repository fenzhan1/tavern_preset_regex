"""通过 GitHub REST API 推送插件源码（github.com 的 git 协议被网络阻断时的替代方案）。

用法：

    python scripts/push_via_api.py --dry-run
    python scripts/push_via_api.py

脚本会：
1. 读取 ``git status --porcelain`` 得到工作区变更；
2. 跳过 ``dist/`` 等构建产物；
3. 用 Git Data API 创建 blob / tree / commit；
4. 更新 ``refs/heads/main`` 与标签 ``refs/tags/v2.3.1``。

Token 从 ``~/.mpdt/config.toml`` 的 ``[github] token`` 读取。
"""

# ruff: noqa: I001 - 该文件既是脚本又是被其他脚本导入的模块

from __future__ import annotations

import argparse
import json
import subprocess
import time
import tomllib
import urllib.error
import urllib.request
from pathlib import Path

PLUGIN_ROOT = Path(__file__).resolve().parents[1]
API = "https://api.github.com"
OWNER = "fenzhan1"
REPO = "tavern_preset_regex"
BRANCH = "main"
TAG = "v2.3.1"
SKIP_PREFIXES = ("dist/",)
SKIP_SUFFIXES = (".pyc", ".pyo")


def load_token() -> str:
    config_path = Path.home() / ".mpdt" / "config.toml"
    with config_path.open("rb") as handle:
        data = tomllib.load(handle)
    token = str(data.get("github", {}).get("token", "") or "")
    if not token:
        raise SystemExit(f"未在 {config_path} 中找到 [github] token")
    return token


def api(
    method: str,
    path: str,
    token: str,
    payload: dict | None = None,
    *,
    retries: int = 4,
) -> dict:
    """调用 GitHub REST API；网络抖动（SSL/连接重置）时自动重试。"""
    body = json.dumps(payload).encode() if payload is not None else None
    last_error: Exception | None = None

    for attempt in range(1, retries + 1):
        request = urllib.request.Request(
            f"{API}{path}",
            data=body,
            method=method,
            headers={
                "Authorization": f"Bearer {token}",
                "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": "2022-11-28",
                "User-Agent": "tavern-preset-regex-push",
                "Content-Type": "application/json",
            },
        )
        try:
            with urllib.request.urlopen(request, timeout=60) as response:
                raw = response.read().decode("utf-8")
                return json.loads(raw) if raw else {}
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", "replace")
            raise SystemExit(
                f"GitHub API {method} {path} 失败: {exc.code} {detail}"
            ) from exc
        except Exception as exc:  # noqa: BLE001 - 网络层抖动统一重试
            last_error = exc
            if attempt < retries:
                wait = 2 * attempt
                print(
                    f"  [api] {method} {path} 第 {attempt} 次失败（{exc}），{wait}s 后重试"
                )
                time.sleep(wait)

    raise SystemExit(
        f"GitHub API {method} {path} 重试 {retries} 次仍失败: {last_error}"
    )


def git(*args: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(PLUGIN_ROOT), "-c", "core.quotepath=false", *args],
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
    )
    if result.returncode != 0:
        raise SystemExit(f"git {' '.join(args)} 失败: {result.stderr.strip()}")
    return result.stdout.strip()


def changed_files() -> list[str]:
    output = git("status", "--porcelain", "-uall")
    files: list[str] = []
    for line in output.splitlines():
        if len(line) < 3:
            continue
        # porcelain v1：前两列是状态码，第三列起（可能带前导空格）是路径。
        path = line[2:].strip()
        if " -> " in path:
            path = path.split(" -> ", 1)[1]
        path = path.strip('"')
        if not path:
            continue
        normalized = path.replace("\\", "/")
        if normalized.startswith(SKIP_PREFIXES) or normalized.endswith(SKIP_SUFFIXES):
            continue
        candidate = PLUGIN_ROOT / path
        if candidate.is_file():
            files.append(normalized)
    return sorted(files)


def build_tree(
    token: str,
    base_tree_sha: str,
    files: list[str],
    parent_message: str,
) -> str:
    entries: list[dict[str, str]] = []
    for path in files:
        content = (PLUGIN_ROOT / path).read_bytes()
        blob = api(
            "POST",
            f"/repos/{OWNER}/{REPO}/git/blobs",
            token,
            {"content": content.decode("utf-8"), "encoding": "utf-8"},
        )
        print(f"  blob {path} -> {blob['sha'][:10]}")
        entries.append(
            {
                "path": path,
                "mode": "100644",
                "type": "blob",
                "sha": blob["sha"],
            }
        )

    tree = api(
        "POST",
        f"/repos/{OWNER}/{REPO}/git/trees",
        token,
        {"base_tree": base_tree_sha, "tree": entries},
    )
    print(f"{parent_message} tree -> {tree['sha'][:10]}")
    return tree["sha"]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    token = load_token()
    files = changed_files()
    print(f"将推送 {len(files)} 个文件：")
    for path in files:
        print(f"  - {path}")

    local_head = git("rev-parse", "HEAD")
    print(f"\n本地 HEAD: {local_head[:10]}")

    if args.dry_run:
        print("\n--dry-run：未执行任何写入")
        return 0

    # 1. 提交工作区变更
    base_tree_sha = git("rev-parse", "HEAD^{tree}")
    tree_sha = build_tree(token, base_tree_sha, files, "提交")
    commit = api(
        "POST",
        f"/repos/{OWNER}/{REPO}/git/commits",
        token,
        {
            "message": "修复 assistant 角色注入并补充测试与诊断脚本",
            "tree": tree_sha,
            "parents": [local_head],
        },
    )
    new_commit = commit["sha"]
    print(f"新提交 -> {new_commit[:10]}")

    # 2. 更新 main 分支
    ref = api("GET", f"/repos/{OWNER}/{REPO}/git/ref/heads/{BRANCH}", token)
    current_remote_sha = ref["object"]["sha"]
    print(f"远端 {BRANCH} 当前 -> {current_remote_sha[:10]}")
    if current_remote_sha != local_head:
        print(
            f"注意：远端 {BRANCH} 与本地 HEAD 不一致，"
            f"仍以本地 HEAD 为父提交（force 更新）"
        )
    api(
        "PATCH",
        f"/repos/{OWNER}/{REPO}/git/refs/heads/{BRANCH}",
        token,
        {"sha": new_commit, "force": True},
    )
    print(f"远端 {BRANCH} 已更新 -> {new_commit[:10]}")

    # 3. 更新版本标签（指向新提交）
    try:
        tag_ref = api("GET", f"/repos/{OWNER}/{REPO}/git/ref/tags/{TAG}", token)
    except SystemExit:
        tag_ref = None
    if tag_ref:
        old_tag_sha = tag_ref["object"]["sha"]
        api(
            "PATCH",
            f"/repos/{OWNER}/{REPO}/git/refs/tags/{TAG}",
            token,
            {"sha": new_commit, "force": True},
        )
        print(f"标签 {TAG} 已移动 {old_tag_sha[:10]} -> {new_commit[:10]}")
    else:
        api(
            "POST",
            f"/repos/{OWNER}/{REPO}/git/refs",
            token,
            {"ref": f"refs/tags/{TAG}", "sha": new_commit},
        )
        print(f"标签 {TAG} 已创建 -> {new_commit[:10]}")

    print("\n完成：源码已通过 API 推送")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
