"""直接调用 WebUI 的 HTTP 接口，定位 Internal Server Error。

用法：

    python scripts/diagnose_webui.py
    python scripts/diagnose_webui.py --data-dir D:\\...\\data\\tavern_preset_regex
"""

from __future__ import annotations

import argparse
import importlib
import io
import sys
import traceback
from pathlib import Path

if hasattr(sys.stdout, "buffer"):
    sys.stdout = io.TextIOWrapper(
        sys.stdout.buffer, encoding="utf-8", errors="replace", line_buffering=True
    )

PLUGIN_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PLUGIN_ROOT.parent))

NEO_MOFOX = Path("D:/Neo-MoFox_Bots/myplugins/neo-mofox")
if NEO_MOFOX.is_dir():
    sys.path.insert(0, str(NEO_MOFOX))

config_module = importlib.import_module("tavern_preset_regex.config")
router_module = importlib.import_module("tavern_preset_regex.router")
store_module = importlib.import_module("tavern_preset_regex.tavern_store")

TavernRegexConfig = config_module.TavernRegexConfig
TavernRegexAdminRouter = router_module.TavernRegexAdminRouter
TavernDataService = store_module.TavernDataService


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--data-dir",
        default=str(NEO_MOFOX / "data" / "tavern_preset_regex"),
    )
    args = parser.parse_args()

    config = TavernRegexConfig()
    config.plugin.data_dir = args.data_dir
    plugin = type(
        "_FakePlugin", (), {"config": config, "plugin_name": "tavern_preset_regex"}
    )()

    print(f"data_dir = {args.data_dir}")
    print("=== 直接调用 TavernDataService（绕过 HTTP） ===")
    service = TavernDataService(plugin=plugin)
    for name, call in (
        ("list_setvar_items", lambda: len(service.list_setvar_items())),
        ("list_ordered_prompt_items", lambda: len(service.list_ordered_prompt_items())),
        ("fixed_prompt_items", lambda: len(service.fixed_prompt_items())),
        ("list_regex_items", lambda: len(service.list_regex_items())),
        ("novel_state", lambda: service.novel_state("")["total"]),
        ("render_setvar_payloads", lambda: len(service.render_setvar_payloads())),
    ):
        try:
            print(f"  {name}: {call()}")
        except Exception:
            print(f"  {name}: 失败")
            traceback.print_exc()

    print("=== 通过 FastAPI TestClient 调用接口 ===")
    router = TavernRegexAdminRouter(plugin)
    try:
        from fastapi.testclient import TestClient
    except ImportError as exc:
        print(f"缺少 httpx/TestClient，跳过 HTTP 测试: {exc}")
        return 0

    client = TestClient(router.app, raise_server_exceptions=False)
    for method, path in (
        ("GET", "/"),
        ("GET", "/api/state"),
        ("GET", "/api/novel"),
        ("GET", "/api/novel?stream_id=test"),
    ):
        response = client.request(method, path)
        print(f"  {method} {path} -> {response.status_code}")
        if response.status_code >= 400:
            print(f"    响应: {response.text[:500]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
