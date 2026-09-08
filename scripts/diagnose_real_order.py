"""用真实 setvar.json 跑一遍注入顺序，检查对话块是否紧贴历史。

用法：

    python scripts/diagnose_real_order.py --setvar <setvar.json> [--order <json文件>]
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
        LLMPayload(ROLE.USER, [Text("历史：conversation_context")]),
        LLMPayload(
            ROLE.ASSISTANT,
            [Text("上轮回复"), ToolCall("call_1", "action-send_text", {"content": "hi"})],
        ),
        LLMPayload(ROLE.TOOL_RESULT, [ToolResult({"status": "已发送消息"}, "call_1")]),
        LLMPayload(ROLE.ASSISTANT, [Text("__SUSPEND__")]),
        LLMPayload(ROLE.USER, [Text("本轮新输入")]),
    ]


def summarize(payloads: list[LLMPayload]) -> list[str]:
    lines: list[str] = []
    for payload in payloads:
        role = str(payload.role).replace("ROLE.", "")
        parts: list[str] = []
        for part in payload.content:
            if isinstance(part, Text):
                parts.append(part.text)
            elif isinstance(part, ToolCall):
                parts.append(f"[tool_call {part.name}]")
            elif isinstance(part, ToolResult):
                parts.append("[tool_result]")
        text = " ".join(parts).replace("\n", " ")[:60]
        lines.append(f"{role:<11} {text}")
    return lines


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--setvar", required=True, help="要测试的 setvar.json")
    parser.add_argument("--order", default="", help="含 mofox_order 的 json（可选）")
    args = parser.parse_args()

    source = Path(args.setvar)
    if not source.is_file():
        print(f"找不到 {source}")
        return 1

    with tempfile.TemporaryDirectory() as tmp:
        work = Path(tmp)
        shutil.copyfile(source, work / "setvar.json")
        data = json.loads((work / "setvar.json").read_text(encoding="utf-8"))
        if args.order:
            extra = json.loads(Path(args.order).read_text(encoding="utf-8"))
            data["mofox_order"] = extra.get("mofox_order") or extra
            (work / "setvar.json").write_text(
                json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
            )

        has_order = "mofox_order" in data
        print(f"setvar: {source}")
        print(f"prompts: {len(data.get('prompts', []))} | mofox_order: {'有' if has_order else '无（回退 prompt_order）'}")

        config = TavernRegexConfig()
        config.plugin.data_dir = str(work)
        config.plugin.main_request_names = ["neo_default_chatter"]
        config.plugin.debug_log = False
        config.novel = NovelSection(enabled=False)
        plugin = type("_FakePlugin", (), {"config": config})()
        service = TavernDataService(tavern_dir=work, plugin=plugin)

        order = service.resolve_mofox_order()
        print(f"解析出的顺序条目数: {len(order)}")
        interesting = (
            "mofox_system",
            "mofox_user",
            "mofox_new_input",
            "mofox_tool",
            "ass",
            "jailbreak",
            "main",
            "novel_current",
        )
        for index, identifier in enumerate(order):
            if identifier in interesting:
                print(f"  {index:>4}  {identifier}")

        handler = TavernRequestHandler(plugin)
        payloads = build_payloads()
        params = {
            "request_name": "neo_default_chatter",
            "payloads": payloads,
            "meta_data": {"stream_id": "real-order-check"},
        }
        asyncio.run(handler.execute("before_llm_request", params))
        result = params["payloads"]

    print("\n注入后的请求顺序（只显示前 30 条）：")
    for index, line in enumerate(summarize(result)[:30]):
        print(f"#{index:<3} {line}")

    joined = "\n".join(summarize(result))
    checks = {
        "回复紧跟历史": joined.index("conversation_context") < joined.index("上轮回复"),
        "工具结果紧跟回复": joined.index("上轮回复") < joined.index("tool_result"),
        "__SUSPEND__ 在工具结果后": joined.index("tool_result") < joined.index("__SUSPEND__"),
        "新输入在 __SUSPEND__ 后": joined.index("__SUSPEND__") < joined.index("本轮新输入"),
    }
    print("\n检查项：")
    failed = False
    for name, ok in checks.items():
        print(f"  [{'OK' if ok else 'FAIL'}] {name}")
        failed = failed or not ok
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
