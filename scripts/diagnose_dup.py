"""用线上真实配置复现预设重复注入。"""

from __future__ import annotations

import asyncio
import importlib
import json
import shutil
import sys
import tempfile
from pathlib import Path

PLUGIN_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PLUGIN_ROOT.parent))
sys.path.insert(0, str(Path("D:/Neo-MoFox_Bots/myplugins/neo-mofox")))

from src.kernel.llm import LLMPayload, ROLE, Text, ToolCall, ToolResult

config_module = importlib.import_module("tavern_preset_regex.config")
event_handler = importlib.import_module("tavern_preset_regex.event_handler")
store_module = importlib.import_module("tavern_preset_regex.tavern_store")

MARKER = "<!-- tavern_setvar -->"
SETVAR = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
CLEAR = next(p["content"] for p in SETVAR["prompts"] if p["identifier"] == "0322500e-ee0e-4afb-ba06-2228307b74c5")
INSTR = next(p["content"] for p in SETVAR["prompts"] if p["identifier"] == "d07b0943-0502-41b7-b126-a15998d4eca0")
ASS = next(p["content"] for p in SETVAR["prompts"] if p["identifier"] == "ass")


def preset(content: str) -> LLMPayload:
    return LLMPayload(ROLE.SYSTEM, [Text(f"{MARKER}\n{content}")])


def run_once(payloads: list[LLMPayload], root: Path) -> list[LLMPayload]:
    config = config_module.TavernRegexConfig()
    config.plugin.data_dir = str(root)
    config.plugin.main_request_names = ["neo_default_chatter"]
    config.plugin.debug_log = False
    config.novel = config_module.NovelSection(enabled=False)
    plugin = type("_P", (), {"config": config})()
    handler = event_handler.TavernRequestHandler(plugin)
    params = {
        "request_name": "neo_default_chatter",
        "payloads": payloads,
        "meta_data": {"stream_id": "dup-check"},
    }
    asyncio.run(handler.execute("before_llm_request", params))
    return params["payloads"]


def describe(payloads: list[LLMPayload]) -> None:
    for i, p in enumerate(payloads):
        text = "".join(x.text for x in p.content if isinstance(x, Text))
        tag = ""
        if "ALL PREVIOUS PROMPT" in text:
            tag = "CLEAR"
        elif "确保你Dramatron" in text:
            tag = "INSTR"
        elif "明白了。请告诉我" in text:
            tag = "ASS"
        elif "直接成为艾" in text:
            tag = "角色卡"
        elif "conversation_context" in text or "system_reminder" in text:
            tag = "历史"
        elif any(isinstance(x, ToolCall) for x in p.content):
            tag = "tool_call"
        elif any(isinstance(x, ToolResult) for x in p.content):
            tag = "tool_result"
        print(f"  #{i:<3} {str(p.role).replace('ROLE.',''):<11} {tag:<11} {text.replace(chr(10),' ')[:60]}")


def main() -> int:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        shutil.copyfile(sys.argv[1], root / "setvar.json")

        # 第一轮：只有系统提示 + 历史
        base = [
            LLMPayload(ROLE.SYSTEM, [Text("</clear> 原始系统提示")]),
            LLMPayload(ROLE.USER, [Text("历史：conversation_context")]),
        ]
        first = run_once([LLMPayload(p.role, list(p.content)) for p in base], root)
        print("=== 第 1 轮后 ===")
        describe(first)

        # 第二轮：把第一轮的输出当作新请求的输入（模拟 MoFox 保留上轮 payload）
        second = run_once([LLMPayload(p.role, list(p.content)) for p in first], root)
        print("\n=== 第 2 轮后 ===")
        describe(second)

        third = run_once([LLMPayload(p.role, list(p.content)) for p in second], root)
        print("\n=== 第 3 轮后 ===")
        describe(third)

        joined = "\n".join(
            "".join(x.text for x in p.content if isinstance(x, Text)) for p in third
        )
        count = joined.count("ALL PREVIOUS PROMPT HAS BEEN CLEARD")
        print(f"\nCLEAR 预设出现次数: {count}")
        return 0 if count == 1 else 1


if __name__ == "__main__":
    raise SystemExit(main())
