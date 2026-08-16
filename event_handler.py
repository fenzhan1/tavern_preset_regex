"""只作用于主回复模型请求与结果的事件处理器。"""

from __future__ import annotations

import hashlib
from typing import Any

from src.app.plugin_system.api.log_api import get_logger
from src.app.plugin_system.api.stream_api import get_stream_info
from src.app.plugin_system.base import BaseEventHandler
from src.app.plugin_system.types import LLMPayload, ROLE, Text, ToolCall
from src.core.components.types import EventType
from src.kernel.event import EventDecision

from .config import TavernRegexConfig
from .engine import apply_rules
from .tavern_store import TavernDataService

logger = get_logger("tavern_preset_regex")

_DEFAULT_MAIN_REQUESTS = {"default_chatter", "neo_default_chatter"}
_SETVAR_MARKER = "<!-- tavern_setvar -->"


def _get_config(plugin: Any) -> TavernRegexConfig:
    """获取插件配置，缺失时回退到默认配置。"""
    config = getattr(plugin, "config", None)
    if isinstance(config, TavernRegexConfig):
        return config
    return TavernRegexConfig()


def _main_request_names(config: TavernRegexConfig) -> set[str]:
    names = getattr(config.plugin, "main_request_names", None) or []
    if not names:
        return _DEFAULT_MAIN_REQUESTS
    return {str(name).strip() for name in names if str(name).strip()}


def _is_main_reply_request(config: TavernRegexConfig, params: dict[str, Any]) -> bool:
    request_name = str(params.get("request_name") or "")
    return request_name in _main_request_names(config)


def _scope_matches(spec: str, chat_type: str, chat_id: str, platform: str = "") -> bool:
    """判断单条作用域字符串是否命中当前聊天。"""
    if ":" not in spec:
        return False
    kind, value = spec.split(":", 1)
    kind = kind.strip().lower()
    value = value.strip()

    if kind == "group":
        if chat_type != "group":
            return False
        return value in ("*", chat_id)
    if kind == "user":
        if chat_type != "private":
            return False
        if value == "*":
            return True
        if platform:
            expected = hashlib.sha256(f"{platform}_{value}".encode()).hexdigest()
            return expected == chat_id
        return value == chat_id
    return False


async def _is_allowed_stream(config: TavernRegexConfig, params: dict[str, Any]) -> bool:
    mode = config.plugin.filter_mode
    if mode == "disabled":
        return True

    meta_data = params.get("meta_data")
    stream_id = str(meta_data.get("stream_id", "") or "") if isinstance(meta_data, dict) else ""
    if not stream_id:
        return True

    try:
        stream_info = await get_stream_info(stream_id)
    except Exception:
        return True
    if not isinstance(stream_info, dict):
        return True

    chat_type = str(stream_info.get("chat_type", ""))
    platform = str(stream_info.get("platform", ""))
    if chat_type == "group":
        chat_id = str(stream_info.get("group_id") or "")
    else:
        chat_id = str(stream_info.get("person_id") or "")

    user_specs = list(config.plugin.user_whitelist) + list(config.plugin.group_whitelist)
    user_excludes = list(config.plugin.user_blacklist) + list(config.plugin.group_blacklist)

    if mode == "whitelist":
        if not user_specs:
            return True
        return any(
            _scope_matches(str(spec), chat_type, chat_id, platform)
            for spec in user_specs
        )
    if mode == "blacklist":
        if not user_excludes:
            return True
        return not any(
            _scope_matches(str(spec), chat_type, chat_id, platform)
            for spec in user_excludes
        )
    return True


def _apply_rules_to_payloads(
    payloads: list[Any],
    rules: list[Any],
    target: str,
) -> None:
    for payload in payloads:
        content = getattr(payload, "content", None)
        if not isinstance(content, list):
            continue
        for index, item in enumerate(content):
            if not isinstance(item, Text):
                continue
            processed = apply_rules(item.text, rules, target)
            if processed != item.text:
                content[index] = Text(processed)


_OUTPUT_TEXT_KEYS = ("content", "text", "message", "prompt")


def _apply_output_to_tool_call(call: Any, rules: list[Any]) -> Any:
    """对工具调用参数中的可见文本字段应用 output 正则。"""
    if isinstance(call, ToolCall):
        args = call.args
        if isinstance(args, dict):
            updated_args = dict(args)
            for key in _OUTPUT_TEXT_KEYS:
                if isinstance(updated_args.get(key), str):
                    processed = apply_rules(updated_args[key], rules, "output")
                    if processed != updated_args[key]:
                        updated_args[key] = processed
            return ToolCall(call.id, call.name, updated_args)
        if isinstance(args, str):
            processed = apply_rules(args, rules, "output")
            if processed != args:
                return ToolCall(call.id, call.name, processed)
        return call

    if isinstance(call, dict):
        updated_call = dict(call)
        args = updated_call.get("args")
        if isinstance(args, dict):
            updated_args = dict(args)
            for key in _OUTPUT_TEXT_KEYS:
                if isinstance(updated_args.get(key), str):
                    processed = apply_rules(updated_args[key], rules, "output")
                    if processed != updated_args[key]:
                        updated_args[key] = processed
            updated_call["args"] = updated_args
        elif isinstance(args, str):
            processed = apply_rules(args, rules, "output")
            if processed != args:
                updated_call["args"] = processed
        return updated_call

    return call


def _inject_setvar_payload_legacy(payloads: list[Any], prompt_text: str) -> None:
    if not prompt_text:
        return
    if any(
        isinstance(item, Text) and _SETVAR_MARKER in item.text
        for payload in payloads
        if str(getattr(payload, "role", "")) == str(ROLE.SYSTEM)
        for item in getattr(payload, "content", []) or []
    ):
        return

    setvar_payload = LLMPayload(
        ROLE.SYSTEM,
        [Text(f"{_SETVAR_MARKER}\n{prompt_text}")],
    )
    last_system_index = -1
    for index, payload in enumerate(payloads):
        if str(getattr(payload, "role", "")) == str(ROLE.SYSTEM):
            last_system_index = index
    payloads.insert(last_system_index + 1, setvar_payload)


def _split_mofox_payloads(
    payloads: list[Any],
) -> tuple[list[Any], list[Any], list[Any]]:
    """把主回复 payload 拆成 system、function 和对话上下文三个固定块。"""
    system_block: list[Any] = []
    function_block: list[Any] = []
    convo_block: list[Any] = []

    for payload in payloads:
        role = str(getattr(payload, "role", ""))
        if role == str(ROLE.SYSTEM):
            system_block.append(payload)
        elif role == str(ROLE.TOOL):
            function_block.append(payload)
        else:
            convo_block.append(payload)

    return system_block, function_block, convo_block


def _contains_setvar_marker(payloads: list[Any]) -> bool:
    return any(
        isinstance(item, Text) and _SETVAR_MARKER in item.text
        for payload in payloads
        for item in getattr(payload, "content", []) or []
    )


def _inject_ordered_setvar_payloads(
    payloads: list[Any],
    service: Any,
) -> None:
    """按 WebUI 的统一顺序重组三个固定块和酒馆预设条目。"""
    if _contains_setvar_marker(payloads):
        return

    rendered_items = service.render_setvar_payloads()
    order_ids = service.resolve_mofox_order(service.load_setvar_payload())
    by_identifier = {
        str(item.get("identifier", "") or ""): item for item in rendered_items
    }

    system_block, function_block, convo_block = _split_mofox_payloads(payloads)
    output: list[Any] = []
    used: set[str] = set()

    for identifier in order_ids:
        if identifier == "mofox_system":
            output.extend(system_block)
            used.add(identifier)
            continue
        if identifier == "mofox_tool":
            output.extend(function_block)
            used.add(identifier)
            continue
        if identifier == "mofox_user":
            output.extend(convo_block)
            used.add(identifier)
            continue

        item = by_identifier.get(identifier)
        if item is None:
            continue
        role = ROLE.USER if str(item.get("role", "")) == "user" else ROLE.SYSTEM
        output.append(
            LLMPayload(
                role,
                [Text(f"{_SETVAR_MARKER}\n{item.get('content', '')}")],
            )
        )
        used.add(identifier)

    for item in rendered_items:
        identifier = str(item.get("identifier", "") or "")
        if identifier and identifier not in used:
            role = ROLE.USER if str(item.get("role", "")) == "user" else ROLE.SYSTEM
            output.append(
                LLMPayload(
                    role,
                    [Text(f"{_SETVAR_MARKER}\n{item.get('content', '')}")],
                )
            )

    if "mofox_system" not in used:
        output.extend(system_block)
    if "mofox_tool" not in used:
        output.extend(function_block)
    if "mofox_user" not in used:
        output.extend(convo_block)

    payloads[:] = output


class TavernRequestHandler(BaseEventHandler):
    """在主回复模型请求发送前注入 setvar 并应用 input 正则。"""

    name: str = "tavern_request_handler"
    description: str = "仅处理发送给主回复模型的 LLM 请求"
    weight: int = 100
    intercept_message: bool = False
    init_subscribe: list[str] = [EventType.BEFORE_LLM_REQUEST]

    async def execute(
        self,
        event_name: str,
        params: dict[str, Any],
    ) -> tuple[EventDecision, dict[str, Any]]:
        config = _get_config(self.plugin)
        if (
            not config.plugin.enabled
            or not _is_main_reply_request(config, params)
            or not await _is_allowed_stream(config, params)
        ):
            return EventDecision.PASS, params

        payloads = params.get("payloads")
        if not isinstance(payloads, list):
            return EventDecision.SUCCESS, params

        if config.plugin.inject_setvar:
            service = TavernDataService(plugin=self.plugin)
            if hasattr(service, "render_setvar_payloads") and hasattr(
                service, "resolve_mofox_order"
            ):
                _inject_ordered_setvar_payloads(payloads, service)
            else:
                _inject_setvar_payload_legacy(
                    payloads,
                    service.render_setvar_prompt(),
                )

        if config.plugin.use_tavern_preset_regex:
            rules = TavernDataService(plugin=self.plugin).list_rules()
            _apply_rules_to_payloads(payloads, rules, "input")

        if config.plugin.debug_log:
            logger.info(
                "主回复模型请求已处理：request_name=%s payloads=%d",
                params.get("request_name"),
                len(payloads),
            )

        return EventDecision.SUCCESS, params


class TavernResponseHandler(BaseEventHandler):
    """在主回复模型返回结果后应用 output 正则。"""

    name: str = "tavern_response_handler"
    description: str = "仅处理主回复模型返回的 LLM 结果"
    weight: int = 100
    intercept_message: bool = False
    init_subscribe: list[str] = [EventType.AFTER_LLM_REQUEST]

    async def execute(
        self,
        event_name: str,
        params: dict[str, Any],
    ) -> tuple[EventDecision, dict[str, Any]]:
        config = _get_config(self.plugin)
        if (
            not config.plugin.enabled
            or not config.plugin.use_tavern_preset_regex
            or not _is_main_reply_request(config, params)
            or not await _is_allowed_stream(config, params)
        ):
            return EventDecision.PASS, params

        rules = TavernDataService(plugin=self.plugin).list_rules()
        tool_calls = params.get("tool_calls")
        if isinstance(tool_calls, list) and tool_calls:
            params["tool_calls"] = [
                _apply_output_to_tool_call(call, rules) for call in tool_calls
            ]
        else:
            message = params.get("message")
            if isinstance(message, str):
                processed = apply_rules(message, rules, "output")
                if processed != message:
                    params["message"] = processed
                    if config.plugin.debug_log:
                        logger.info(
                            "主回复模型结果已处理：request_name=%s",
                            params.get("request_name"),
                        )

        return EventDecision.SUCCESS, params
