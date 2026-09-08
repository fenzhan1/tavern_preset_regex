"""诊断 setvar 预设条目在注入主回复请求时的角色与顺序。

用法（在插件目录下执行）：

    python scripts/diagnose_role.py

脚本会把 neo-mofox 的 src 加入 PYTHONPATH（也可自己设置），随后读取
data/tavern_preset_regex/setvar.json，模拟一次主回复请求的 payload，
打印注入后的角色序列，并做一次 MoFox 的上下文结构校验。
"""

# ruff: noqa: I001 - 需要先补齐 sys.path 才能导入 neo-mofox 与插件模块

from __future__ import annotations

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
from src.kernel.llm.context_structure import validate_payload_sequence

# 只加载需要的子模块，避免执行插件包的 __init__.py。
event_handler = importlib.import_module("tavern_preset_regex.event_handler")
tavern_store = importlib.import_module("tavern_preset_regex.tavern_store")

_inject_ordered_setvar_payloads = event_handler._inject_ordered_setvar_payloads
TavernDataService = tavern_store.TavernDataService


def build_payloads() -> list[LLMPayload]:
    """模拟一次主回复请求的 payload 结构。"""
    return [
        LLMPayload(ROLE.SYSTEM, [Text("系统提示词")]),
        LLMPayload(ROLE.TOOL, [Text("工具声明占位")]),
        LLMPayload(ROLE.USER, [Text("用户历史消息 1")]),
        LLMPayload(ROLE.ASSISTANT, [Text("机器人历史回复 1")]),
        LLMPayload(ROLE.USER, [Text("当前用户输入")]),
    ]


def dump(payloads: list[LLMPayload]) -> None:
    for index, payload in enumerate(payloads):
        text = "".join(part.text for part in payload.content if isinstance(part, Text))
        preview = text.replace("\n", "\\n")[:60]
        print(f"  [{index:>3}] {payload.role!s:<14} {preview}")


def main() -> int:
    service = TavernDataService(tavern_dir=NEO_MOFOX / "data" / "tavern_preset_regex")
    payloads = build_payloads()
    demoted = _inject_ordered_setvar_payloads(payloads, service)

    print(f"注入后共 {len(payloads)} 条 payload：")
    dump(payloads)
    if demoted:
        print(f"\n被降级为 system 的 assistant 条目：{', '.join(demoted)}")

    try:
        validate_payload_sequence(payloads, allow_incomplete_tail=False)
        print("\n结构校验：通过")
    except Exception as exc:  # noqa: BLE001
        print(f"\n结构校验：失败 -> {exc}")
        return 1

    print("\nassistant 条目检查：")
    assistant_items = [
        payload for payload in payloads if payload.role == ROLE.ASSISTANT
    ]
    if not assistant_items:
        print("  没有 assistant payload（可能全部被降级或未启用）")
    for payload in assistant_items:
        text = "".join(part.text for part in payload.content if isinstance(part, Text))
        preview = text.replace("\n", "\\n")[:80]
        print(f"  {preview}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
