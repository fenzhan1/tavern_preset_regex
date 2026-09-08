"""复现用户日志里的请求顺序，检查「回复 + 工具结果 + 收尾 + 新输入」是否紧贴历史。

用法：

    python scripts/diagnose_order.py --data-dir D:\\...\\日志 [--order-file setvar.json]
"""

# ruff: noqa: I001 - 需要先补齐 sys.path 才能导入 neo-mofox 与插件模块

from __future__ import annotations

import argparse
import asyncio
import importlib
import io
import json
import shutil
import sys
import tempfile
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

from src.kernel.llm import LLMPayload, ROLE, Text, ToolCall, ToolResult

config_module = importlib.import_module("tavern_preset_regex.config")
event_handler = importlib.import_module("tavern_preset_regex.event_handler")
store_module = importlib.import_module("tavern_preset_regex.tavern_store")

TavernRegexConfig = config_module.TavernRegexConfig
NovelSection = config_module.NovelSection
TavernDataService = store_module.TavernDataService
TavernRequestHandler = event_handler.TavernRequestHandler


def build_payloads() -> list[LLMPayload]:
    """按用户「请求内容.txt」的顺序构造对话块。"""
    return [
        LLMPayload(ROLE.SYSTEM, [Text("</clear>")]),
        LLMPayload(ROLE.USER, [Text("<instructions> 角色引导")]),
        LLMPayload(ROLE.SYSTEM, [Text("MoFox系统提示词")]),
        LLMPayload(
            ROLE.USER,
            [Text("conversation_context + latest_events + transport_context")],
        ),
        LLMPayload(
            ROLE.ASSISTANT,
            [
                Text("上轮回复"),
                ToolCall("call_1", "action-send_text", {"content": "hi"}),
            ],
        ),
        LLMPayload(ROLE.TOOL_RESULT, [ToolResult({"status": "已发送消息"}, "call_1")]),
        LLMPayload(ROLE.ASSISTANT, [Text("__SUSPEND__")]),
        LLMPayload(ROLE.USER, [Text("本轮新输入")]),
    ]


def render(payloads: list[LLMPayload]) -> str:
    labels: list[str] = []
    for payload in payloads:
        role = str(payload.role)
        text = "".join(part.text for part in payload.content if isinstance(part, Text))
        if any(isinstance(part, ToolCall) for part in payload.content):
            text = f"{text} [tool_call]"
        if any(isinstance(part, ToolResult) for part in payload.content):
            text = "工具结果"
        text = text.replace("\n", " ")[:40]
        labels.append(f"{role:<11} {text}")
    return "\n".join(f"#{index} {line}" for index, line in enumerate(labels))


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--data-dir", required=True, help="含 setvar.json 的目录")
    parser.add_argument("--order-file", default="setvar.json")
    args = parser.parse_args()

    source = Path(args.data_dir)
    order_file = source / args.order_file
    if not order_file.is_file():
        print(f"找不到 {order_file}")
        return 1

    with tempfile.TemporaryDirectory() as tmp:
        work = Path(tmp)
        shutil.copyfile(order_file, work / "setvar.json")
        data = json.loads((work / "setvar.json").read_text(encoding="utf-8"))
        order = data.get("mofox_order") or []
        print("mofox_order：")
        for index, identifier in enumerate(order):
            print(f"  {index:>2}  {identifier}")

        config = TavernRegexConfig()
        config.plugin.data_dir = str(work)
        config.plugin.main_request_names = ["neo_default_chatter"]
        config.plugin.debug_log = False
        config.novel = NovelSection(enabled=False)
        plugin = type("_FakePlugin", (), {"config": config})()
        service = TavernDataService(tavern_dir=work, plugin=plugin)

        handler = TavernRequestHandler(plugin)
        payloads = build_payloads()
        params = {
            "request_name": "neo_default_chatter",
            "payloads": payloads,
            "meta_data": {"stream_id": "order-check"},
        }
        asyncio.run(handler.execute("before_llm_request", params))
        result = params["payloads"]

    print("\n注入后的请求顺序：")
    print(render(result))

    order_text = render(result)
    checks = {
        "回复紧跟历史": "conversation_context" in order_text
        and order_text.index("conversation_context") < order_text.index("上轮回复"),
        "工具结果紧跟回复": order_text.index("上轮回复") < order_text.index("工具结果"),
        "__SUSPEND__ 在工具结果后": order_text.index("工具结果")
        < order_text.index("__SUSPEND__"),
        "新输入在 __SUSPEND__ 后": order_text.index("__SUSPEND__")
        < order_text.index("本轮新输入"),
        "预填充在新输入之后": order_text.index("本轮新输入")
        < order_text.index("明白了。"),
    }
    print("\n检查项：")
    failed = False
    for name, ok in checks.items():
        print(f"  [{'OK' if ok else 'FAIL'}] {name}")
        failed = failed or not ok
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
