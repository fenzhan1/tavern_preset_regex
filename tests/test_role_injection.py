"""tavern_preset_regex 酒馆预设条目角色注入的回归测试。

运行方式（使用 neo-mofox 的虚拟环境）：

    D:\\Neo-MoFox_Bots\\myplugins\\neo-mofox\\.venv\\Scripts\\python.exe -m pytest tests
"""

# ruff: noqa: I001 - 需要先补齐 sys.path 才能导入 neo-mofox 与插件模块

from __future__ import annotations

import importlib
import sys
from pathlib import Path

import pytest

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
_inject_ordered_setvar_payloads = event_handler._inject_ordered_setvar_payloads
_tavern_role_to_role = event_handler._tavern_role_to_role


class FakeService:
    """只实现注入流程需要的几个方法。"""

    def __init__(self, items: list[dict[str, object]], order: list[str]) -> None:
        self._items = items
        self._order = order

    def render_setvar_payloads(
        self,
        *,
        seed_variables: dict[str, str] | None = None,
        include_novel: bool = False,
        conversation: str = "",
    ) -> list[dict[str, object]]:
        return [dict(item) for item in self._items]

    def resolve_mofox_order(self, payload: object = None) -> list[str]:
        return list(self._order)

    def load_setvar_payload(self) -> dict[str, object]:
        return {}


def make_item(identifier: str, role: str, content: str = "内容") -> dict[str, object]:
    return {
        "identifier": identifier,
        "name": f"条目 {identifier}",
        "role": role,
        "content": content,
    }


def base_payloads() -> list[LLMPayload]:
    return [
        LLMPayload(ROLE.SYSTEM, [Text("系统提示词")]),
        LLMPayload(ROLE.TOOL, [Text("工具占位")]),
        LLMPayload(ROLE.USER, [Text("历史用户消息")]),
        LLMPayload(ROLE.ASSISTANT, [Text("历史机器人消息")]),
        LLMPayload(ROLE.USER, [Text("当前用户输入")]),
    ]


def test_role_mapping() -> None:
    assert _tavern_role_to_role("user") is ROLE.USER
    assert _tavern_role_to_role("assistant") is ROLE.ASSISTANT
    assert _tavern_role_to_role("Assistant") is ROLE.ASSISTANT
    assert _tavern_role_to_role("system") is ROLE.SYSTEM
    assert _tavern_role_to_role("") is ROLE.SYSTEM


def test_assistant_entry_keeps_assistant_role() -> None:
    """排在用户上下文之后的 assistant 条目必须按 assistant 角色注入。"""
    service = FakeService(
        [make_item("preset-a", "assistant", "<think>已完成</think>")],
        ["mofox_system", "mofox_tool", "mofox_user", "preset-a"],
    )
    payloads = base_payloads()
    demoted = _inject_ordered_setvar_payloads(payloads, service)

    assert demoted == []
    assistant_payloads = [
        payload for payload in payloads if payload.role == ROLE.ASSISTANT
    ]
    assert len(assistant_payloads) == 2
    texts = [
        part.text
        for payload in assistant_payloads
        for part in payload.content
        if isinstance(part, Text)
    ]
    assert any("<think>已完成</think>" in text for text in texts)
    validate_payload_sequence(payloads, allow_incomplete_tail=False)


def test_assistant_entry_after_user_block_is_valid() -> None:
    """assistant 条目夹在用户上下文与工具块之间时同样保持 assistant 角色。"""
    service = FakeService(
        [make_item("preset-mid", "assistant", "预填充")],
        ["mofox_system", "mofox_user", "preset-mid", "mofox_tool"],
    )
    payloads = base_payloads()
    demoted = _inject_ordered_setvar_payloads(payloads, service)

    assert demoted == []
    validate_payload_sequence(payloads, allow_incomplete_tail=False)
    assert any(payload.role == ROLE.ASSISTANT for payload in payloads)


def test_assistant_entry_before_user_is_demoted() -> None:
    """assistant 条目排在对话之前时降级为 system，保证序列合法。"""
    service = FakeService(
        [make_item("preset-early", "assistant", "预填充")],
        ["preset-early", "mofox_system", "mofox_tool", "mofox_user"],
    )
    payloads = base_payloads()
    demoted = _inject_ordered_setvar_payloads(payloads, service)

    assert demoted == ["条目 preset-early"]
    assert str(payloads[0].role) == str(ROLE.SYSTEM)
    validate_payload_sequence(payloads, allow_incomplete_tail=False)


def test_consecutive_assistant_entries_demote_second_one() -> None:
    """两条连续的 assistant 条目只保留第一条为 assistant。"""
    service = FakeService(
        [
            make_item("preset-1", "assistant", "第一条"),
            make_item("preset-2", "assistant", "第二条"),
        ],
        ["mofox_system", "mofox_tool", "mofox_user", "preset-1", "preset-2"],
    )
    payloads = base_payloads()
    demoted = _inject_ordered_setvar_payloads(payloads, service)

    assert demoted == ["条目 preset-2"]
    validate_payload_sequence(payloads, allow_incomplete_tail=False)


def test_user_entry_keeps_user_role() -> None:
    service = FakeService(
        [make_item("preset-user", "user", "强调输入")],
        ["mofox_system", "preset-user", "mofox_tool", "mofox_user"],
    )
    payloads = base_payloads()
    _inject_ordered_setvar_payloads(payloads, service)

    assert str(payloads[1].role) == str(ROLE.USER)
    validate_payload_sequence(payloads, allow_incomplete_tail=False)


@pytest.mark.parametrize("role", ["system", "user", "assistant"])
def test_fixed_blocks_are_preserved(role: str) -> None:
    service = FakeService(
        [make_item("preset-x", role, "内容")],
        ["mofox_system", "preset-x", "mofox_tool", "mofox_user"],
    )
    payloads = base_payloads()
    _inject_ordered_setvar_payloads(payloads, service)

    texts = [
        part.text
        for payload in payloads
        for part in payload.content
        if isinstance(part, Text)
    ]
    assert "系统提示词" in texts
    assert "工具占位" in texts
    assert "当前用户输入" in texts
