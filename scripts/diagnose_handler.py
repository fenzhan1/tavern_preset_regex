"""端到端验证 TavernRequestHandler 对真实 setvar.json 的处理结果。

用法（在插件目录下执行）：

    python scripts/diagnose_role.py
"""

# ruff: noqa: I001 - 需要先补齐 sys.path 才能导入 neo-mofox 与插件模块

from __future__ import annotations

import asyncio
import importlib
import io
import os
import sys
from pathlib import Path
from types import SimpleNamespace

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
from src.kernel.llm.context_structure import (
    validate_payload_sequence,
)

# 只加载需要的子模块，避免执行插件包的 __init__.py。
event_handler = importlib.import_module("tavern_preset_regex.event_handler")
TavernRequestHandler = event_handler.TavernRequestHandler


def build_plugin() -> SimpleNamespace:
    """构造一个只带插件配置的假插件对象。

    可用环境变量 ``TAVERN_DIAG_DATA_DIR`` 覆盖 data_dir，便于检查其他
    机器人实例（绝对路径或相对当前工作目录的路径）。
    """
    data_dir = os.environ.get("TAVERN_DIAG_DATA_DIR", "data/tavern_preset_regex")
    plugin_config = SimpleNamespace(
        enabled=True,
        data_dir=data_dir,
        inject_setvar=True,
        use_tavern_preset_regex=True,
        main_request_names=["default_chatter", "neo_default_chatter"],
        filter_mode="disabled",
        user_whitelist=[],
        user_blacklist=[],
        group_whitelist=[],
        group_blacklist=[],
        debug_log=True,
    )
    return SimpleNamespace(
        plugin_name="tavern_preset_regex",
        config=SimpleNamespace(plugin=plugin_config),
    )


def build_payloads() -> list[LLMPayload]:
    return [
        LLMPayload(ROLE.SYSTEM, [Text("系统提示词")]),
        LLMPayload(ROLE.TOOL, [Text("工具声明占位")]),
        LLMPayload(ROLE.USER, [Text("用户历史消息 1")]),
        LLMPayload(ROLE.ASSISTANT, [Text("机器人历史回复 1")]),
        LLMPayload(ROLE.USER, [Text("当前用户输入")]),
    ]


async def main() -> int:
    handler = TavernRequestHandler(build_plugin())
    payloads = build_payloads()
    params = {
        "request_name": "neo_default_chatter",
        "payloads": payloads,
        "meta_data": {},
    }
    decision, result = await handler.execute("before_llm_request", params)
    payloads = result["payloads"]

    print(f"decision={decision} payloads={len(payloads)}")
    role_counts: dict[str, int] = {}
    for payload in payloads:
        key = str(payload.role)
        role_counts[key] = role_counts.get(key, 0) + 1
    print("角色统计：", role_counts)

    assistant_items = [
        payload for payload in payloads if payload.role == ROLE.ASSISTANT
    ]
    print(f"assistant payload 数量：{len(assistant_items)}")
    for payload in assistant_items:
        text = "".join(part.text for part in payload.content if isinstance(part, Text))
        preview = text.replace("\n", "\\n")[:80]
        print(f"  - {preview}")

    try:
        validate_payload_sequence(payloads, allow_incomplete_tail=False)
        print("结构校验：通过")
        return 0
    except Exception as exc:  # noqa: BLE001
        print(f"结构校验：失败 -> {exc}")
        return 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
