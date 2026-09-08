"""通过 SSH 在远程主机执行命令（密码登录）。

用法：

    python scripts/ssh_run.py "uname -a"
    python scripts/ssh_run.py --host 1.2.3.4 --user root --password xxx "ls -la"
    python scripts/ssh_run.py --put local.txt /root/remote.txt
    python scripts/ssh_run.py --get /root/remote.txt local.txt

凭据默认从环境变量 SSH_HOST / SSH_USER / SSH_PASSWORD 读取。
"""

from __future__ import annotations

import argparse
import io
import os
import sys
from pathlib import Path

if hasattr(sys.stdout, "buffer"):
    sys.stdout = io.TextIOWrapper(
        sys.stdout.buffer, encoding="utf-8", errors="replace", line_buffering=True
    )

import paramiko


def connect(host: str, port: int, user: str, password: str) -> paramiko.SSHClient:
    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    client.connect(
        hostname=host,
        port=port,
        username=user,
        password=password,
        timeout=20,
        banner_timeout=30,
        auth_timeout=30,
        look_for_keys=False,
        allow_agent=False,
    )
    return client


def run(client: paramiko.SSHClient, command: str) -> int:
    stdin, stdout, stderr = client.exec_command(command, timeout=120)
    stdin.close()
    out = stdout.read().decode("utf-8", "replace")
    err = stderr.read().decode("utf-8", "replace")
    code = stdout.channel.recv_exit_status()
    if out:
        print(out, end="" if out.endswith("\n") else "\n")
    if err:
        print("[stderr]", file=sys.stderr)
        print(err, end="" if err.endswith("\n") else "\n", file=sys.stderr)
    print(f"[exit {code}]")
    return code


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default=os.environ.get("SSH_HOST", ""))
    parser.add_argument("--port", type=int, default=int(os.environ.get("SSH_PORT", "22")))
    parser.add_argument("--user", default=os.environ.get("SSH_USER", "root"))
    parser.add_argument("--password", default=os.environ.get("SSH_PASSWORD", ""))
    parser.add_argument("--put", nargs=2, metavar=("LOCAL", "REMOTE"))
    parser.add_argument("--get", nargs=2, metavar=("REMOTE", "LOCAL"))
    parser.add_argument(
        "--script",
        default="",
        help="把本地脚本文件上传到远端 /tmp 后执行（避免命令行转义问题）",
    )
    parser.add_argument("command", nargs="*")
    args = parser.parse_args()

    if not args.host or not args.password:
        print("缺少主机或密码：用 --host/--password 或 SSH_HOST/SSH_PASSWORD 环境变量")
        return 2

    client = connect(args.host, args.port, args.user, args.password)
    try:
        if args.put:
            sftp = client.open_sftp()
            sftp.put(args.put[0], args.put[1])
            sftp.close()
            print(f"已上传 {args.put[0]} -> {args.put[1]}")
        if args.get:
            sftp = client.open_sftp()
            sftp.get(args.get[0], args.get[1])
            sftp.close()
            print(f"已下载 {args.get[0]} -> {args.get[1]}")
        if args.script:
            local = Path(args.script)
            if not local.is_file():
                print(f"找不到脚本 {local}")
                return 2
            remote = f"/tmp/_dsh_{local.stem}.sh"
            sftp = client.open_sftp()
            sftp.put(str(local), remote)
            sftp.close()
            return run(client, f"bash {remote}")
        if args.command:
            return run(client, " ".join(args.command))
        return 0
    finally:
        client.close()


if __name__ == "__main__":
    raise SystemExit(main())
