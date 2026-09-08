"""验证小说注入角色确实跟随设置（用真实数据目录跑一遍，跑完还原）。

用法：

    python scripts/diagnose_novel_role_live.py --data-dir D:\\...\\data\\tavern_preset_regex
"""

# ruff: noqa: I001 - 需要先补齐 sys.path 才能导入 neo-mofox 与插件模块

from __future__ import annotations

import argparse
import asyncio
import importlib
import io
import sys
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

from src.kernel.llm import LLMPayload, ROLE, Text

config_module = importlib.import_module("tavern_preset_regex.config")
event_handler = importlib.import_module("tavern_preset_regex.event_handler")
store_module = importlib.import_module("tavern_preset_regex.tavern_store")

TavernRegexConfig = config_module.TavernRegexConfig
NovelSection = config_module.NovelSection
TavernDataService = store_module.TavernDataService
TavernRequestHandler = event_handler.TavernRequestHandler


def run_request(service: TavernDataService, plugin, stream_id: str) -> list[LLMPayload]:
    handler = TavernRequestHandler(plugin)
    payloads = [
        LLMPayload(ROLE.SYSTEM, [Text("系统提示词")]),
        LLMPayload(ROLE.TOOL, [Text("工具占位")]),
        LLMPayload(ROLE.USER, [Text("用户输入")]),
    ]
    params = {
        "request_name": "neo_default_chatter",
        "payloads": payloads,
        "meta_data": {"stream_id": stream_id},
    }
    asyncio.run(handler.execute("before_llm_request", params))
    return params["payloads"]


def find_novel_payload(payloads: list[LLMPayload]) -> LLMPayload | None:
    for payload in payloads:
        text = "".join(part.text for part in payload.content if isinstance(part, Text))
        # 小说条目：setvar 标记后紧跟章节正文，且不是预设里的 <novel> 包裹条目
        if (
            "tavern_setvar" in text
            and "<novel>" not in text
            and any(marker in text for marker in ("第", "章", "节", "卷", "段", "序"))
        ):
            return payload
    return None


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--data-dir", required=True)
    parser.add_argument(
        "--novel",
        default="",
        help="指定小说文件名；留空时用目录里第一个小说",
    )
    args = parser.parse_args()

    data_dir = Path(args.data_dir)
    config = TavernRegexConfig()
    config.plugin.data_dir = str(data_dir)
    config.plugin.main_request_names = ["neo_default_chatter"]
    config.plugin.debug_log = False
    config.novel = NovelSection(enabled=True, role="system")
    plugin = type("_FakePlugin", (), {"config": config})()
    service = TavernDataService(tavern_dir=data_dir, plugin=plugin)

    if args.novel:
        service.save_novel_settings({"file": args.novel})

    state = service.novel_state("live-role-check")
    if not state["active_file"]:
        print(f"novel/ 目录里没有小说：{state['dir']}")
        print("把 .txt/.md 小说放进去后再跑本脚本。")
        return 0
    print(f"小说：{state['active_file']}（{state['total']} 段）")

    backup = None
    cfg_path = data_dir / "novel" / "config.json"
    if cfg_path.is_file():
        backup = cfg_path.read_text(encoding="utf-8")

    try:
        for role in ("system", "user", "assistant"):
            service.save_novel_settings({"role": role})
            payloads = run_request(service, plugin, f"live-role-{role}")
            novel_payload = find_novel_payload(payloads)
            if novel_payload is None:
                print(f"  role={role}: 没找到小说条目（可能未启用或已读完）")
                continue
            print(
                f"  config role={role:<9} -> 注入 payload 角色 = {novel_payload.role}"
            )
    finally:
        if backup is None:
            cfg_path.unlink(missing_ok=True)
            print("已还原：删除临时 config.json")
        else:
            cfg_path.write_text(backup, encoding="utf-8")
            print("已还原：恢复原 config.json")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
