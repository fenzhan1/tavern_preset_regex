"""验证幂等重排：二次请求（已带注入预设）也要按顺序表重排。"""

from __future__ import annotations

import asyncio
import importlib
import json
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

TavernRegexConfig = config_module.TavernRegexConfig
NovelSection = config_module.NovelSection
TavernDataService = store_module.TavernDataService
TavernRequestHandler = event_handler.TavernRequestHandler

MARKER = "<!-- tavern_setvar -->"
SETVAR = {
    "prompts": [
        {
            "identifier": "prefill",
            "name": "ass预设",
            "role": "assistant",
            "content": "明白了。请告诉我接下来的具体要求。",
            "enabled": True,
        },
        {
            "identifier": "tail",
            "name": "自定义user",
            "role": "user",
            "content": "然后直接开始输出",
            "enabled": True,
        },
    ],
    "prompt_order": [
        {"identifier": "prefill", "enabled": True},
        {"identifier": "tail", "enabled": True},
    ],
    "mofox_order": [
        "mofox_system",
        "mofox_user",
        "mofox_new_input",
        "prefill",
        "tail",
        "mofox_tool",
    ],
}


def build_followup_payloads() -> list[LLMPayload]:
    """模拟线上 #29 的结构：两轮工具调用，预设已在 payload 里。"""
    return [
        LLMPayload(ROLE.SYSTEM, [Text(f"{MARKER}\n</clear> 预设一")]),
        LLMPayload(ROLE.USER, [Text(f"{MARKER}\n角色引导 预设二")]),
        LLMPayload(ROLE.SYSTEM, [Text("MoFox系统提示词")]),
        LLMPayload(ROLE.USER, [Text("历史：conversation_context")]),
        # 第一次工具调用：assistant 带 tool_call + 预填充文本
        LLMPayload(
            ROLE.ASSISTANT,
            [
                Text(f"{MARKER}\n明白了。请告诉我接下来的具体要求。"),
                ToolCall("call_1", "action-send_text", {"content": "hi"}),
            ],
        ),
        LLMPayload(ROLE.TOOL_RESULT, [ToolResult({"status": "已发送消息"}, "call_1")]),
        LLMPayload(ROLE.ASSISTANT, [Text("__SUSPEND__")]),
        # 第二次工具调用（线上 #10~#13）
        LLMPayload(ROLE.USER, [Text("第二轮新输入")]),
        LLMPayload(
            ROLE.ASSISTANT,
            [ToolCall("call_2", "action-send_text", {"content": "yo"})],
        ),
        LLMPayload(ROLE.TOOL_RESULT, [ToolResult({"status": "已发送消息"}, "call_2")]),
        LLMPayload(ROLE.ASSISTANT, [Text("__SUSPEND__")]),
        LLMPayload(ROLE.USER, [Text("本轮新输入")]),
    ]


def run() -> int:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        (root / "setvar.json").write_text(
            json.dumps(SETVAR, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        config = TavernRegexConfig()
        config.plugin.data_dir = str(root)
        config.plugin.main_request_names = ["neo_default_chatter"]
        config.plugin.debug_log = False
        config.novel = NovelSection(enabled=False)
        plugin = type("_FakePlugin", (), {"config": config})()

        payloads = build_followup_payloads()
        handler = TavernRequestHandler(plugin)
        params = {
            "request_name": "neo_default_chatter",
            "payloads": payloads,
            "meta_data": {"stream_id": "followup-check"},
        }
        asyncio.run(handler.execute("before_llm_request", params))
        out = params["payloads"]

    print("二次请求注入后的顺序：")
    for index, payload in enumerate(out):
        text = "".join(p.text for p in payload.content if isinstance(p, Text))
        if any(isinstance(p, ToolCall) for p in payload.content):
            text += " [tool_call]"
        if any(isinstance(p, ToolResult) for p in payload.content):
            text = "[tool_result]"
        print(
            f"  #{index:<3} {str(payload.role).replace('ROLE.', ''):<11} "
            f"{text.replace(chr(10), ' ')[:70]}"
        )

    joined = "\n".join(
        "".join(p.text for p in payload.content if isinstance(p, Text))
        for payload in out
    )
    checks = {
        "回复紧跟历史": joined.index("conversation_context") < joined.index("明白了。"),
        "工具结果在回复后": joined.index("明白了。") < joined.index("__SUSPEND__"),
        "新输入在收尾后": joined.index("__SUSPEND__") < joined.index("本轮新输入"),
        "预填充在新输入之后": joined.index("本轮新输入") < joined.index("然后直接开始输出"),
        # 二次请求不能再新增一份预设（复用已有 payload 而不是重新注入）
        "预设未重复注入": joined.count("然后直接开始输出") == 1,
    }
    print("\n检查项：")
    failed = False
    for name, ok in checks.items():
        print(f"  [{'OK' if ok else 'FAIL'}] {name}")
        failed = failed or not ok
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(run())
