"""只作用于主回复模型请求与结果的事件处理器。"""

from __future__ import annotations

import hashlib
from typing import Any

from src.app.plugin_system.api.log_api import get_logger
from src.app.plugin_system.api.stream_api import get_stream_info
from src.app.plugin_system.base import BaseEventHandler
from src.app.plugin_system.types import ROLE, LLMPayload, Text, ToolCall
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


def _stream_id(params: dict[str, Any]) -> str:
    """从事件参数里取聊天流 ID，用于隔离小说进度。"""
    meta_data = params.get("meta_data")
    if isinstance(meta_data, dict):
        return str(meta_data.get("stream_id", "") or "")
    return ""


def _novel_config(config: TavernRegexConfig) -> Any:
    return getattr(config, "novel", None)


def _inject_novel_segment(
    config: TavernRegexConfig,
    params: dict[str, Any],
    service: TavernDataService,
) -> dict[str, Any] | None:
    """取当前小说段落并推进进度。

    返回 ``{"content", "start", "end", "total", "file", "finished", "looped"}``；
    未启用或没有小说文件时返回 ``None``。
    """
    settings = service.novel_settings()
    if not bool(settings.get("enabled")):
        return None

    try:
        state = service.novel_state(_stream_id(params))
    except ValueError as exc:
        logger.warning(f"读取小说失败，跳过小说注入: {exc}")
        return None

    segments: list[str] = list(state.get("segments") or [])
    active = str(state.get("active_file", "") or "")
    if not active or not segments:
        return None

    result = service.novel_service().advance(
        active,
        segments,
        batch_size=int(settings.get("batch_size", 1) or 1),
        loop=bool(settings.get("loop", False)),
        stream_id=_stream_id(params),
    )
    result["file"] = active
    return result


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
    stream_id = (
        str(meta_data.get("stream_id", "") or "") if isinstance(meta_data, dict) else ""
    )
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

    user_specs = list(config.plugin.user_whitelist) + list(
        config.plugin.group_whitelist
    )
    user_excludes = list(config.plugin.user_blacklist) + list(
        config.plugin.group_blacklist
    )

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


def _tavern_role_to_role(raw_role: Any) -> ROLE:
    """把酒馆预设条目声明的角色映射成 MoFox 的 LLM 角色。"""
    name = str(raw_role or "").strip().lower()
    if name == "user":
        return ROLE.USER
    if name == "assistant":
        return ROLE.ASSISTANT
    return ROLE.SYSTEM


def _build_setvar_payload(role: ROLE, content: Any) -> LLMPayload:
    """构造一条带 setvar 标记的酒馆预设 payload。"""
    return LLMPayload(role, [Text(f"{_SETVAR_MARKER}\n{content}")])


def _demote_invalid_assistant_payloads(
    payloads: list[Any],
    names: dict[int, str] | None = None,
) -> list[str]:
    """把结构上不合法的 assistant 条目降级为 system，并返回被降级的条目名。

    MoFox 的上下文校验要求 assistant 不能出现在对话开头，也不能紧跟在另一条
    assistant 之后。用户可以把酒馆预设条目排到任意位置，因此这里做一次兜底，
    保证注入后的 payload 序列仍然能被主回复请求接受。
    """
    demoted: list[str] = []
    previous_convo_role = ""
    names = names or {}

    for index, payload in enumerate(payloads):
        role = str(getattr(payload, "role", ""))
        if role == str(ROLE.ASSISTANT):
            if previous_convo_role not in (str(ROLE.USER), str(ROLE.TOOL_RESULT)):
                content = getattr(payload, "content", None)
                if isinstance(content, list):
                    payloads[index] = LLMPayload(ROLE.SYSTEM, content)
                else:
                    payloads[index] = LLMPayload(ROLE.SYSTEM, [Text(str(content))])
                demoted.append(names.get(index, f"#{index}"))
                continue
            previous_convo_role = role
            continue

        # system / tool 不参与对话结构校验，不更新前置角色。
        if role in (str(ROLE.USER), str(ROLE.TOOL_RESULT)):
            previous_convo_role = role

    return demoted


def _inject_ordered_setvar_payloads(
    payloads: list[Any],
    service: Any,
    *,
    config: TavernRegexConfig | None = None,
    novel: dict[str, Any] | None = None,
) -> list[str]:
    """按 WebUI 的统一顺序重组三个固定块、酒馆预设条目与小说当前段落。

    ``novel`` 为本次要注入的小说段落信息（由 :func:`_inject_novel_segment`
    取得），其内容会作为 ``{{getvar::变量名}}`` 的取值参与渲染。

    返回被降级为 system 的 assistant 条目名称列表，便于调用方记录日志。
    """
    if _contains_setvar_marker(payloads):
        return []

    settings: dict[str, Any] = {}
    if hasattr(service, "novel_settings"):
        try:
            settings = service.novel_settings() or {}
        except Exception:  # noqa: BLE001 - 小说配置异常不影响预设注入
            settings = {}
    variable_name = str(settings.get("variable_name") or "current_chapter")
    seed: dict[str, str] = {}
    content = str((novel or {}).get("content", "") or "")
    if content:
        seed[variable_name] = content

    entry_enabled = bool(settings.get("entry_enabled", True))
    inject_when_empty = bool(settings.get("inject_when_empty", False))
    include_novel = entry_enabled and (bool(content) or inject_when_empty)

    rendered_items = service.render_setvar_payloads(
        seed_variables=seed,
        include_novel=include_novel,
    )
    order_ids = service.resolve_mofox_order(service.load_setvar_payload())
    by_identifier = {
        str(item.get("identifier", "") or ""): item for item in rendered_items
    }

    system_block, function_block, convo_block = _split_mofox_payloads(payloads)
    output: list[Any] = []
    used: set[str] = set()
    preset_names: dict[int, str] = {}

    def append_preset(identifier: str, item: dict[str, Any]) -> None:
        preset_names[len(output)] = str(item.get("name", "") or identifier)
        output.append(
            _build_setvar_payload(
                _tavern_role_to_role(item.get("role")),
                item.get("content", ""),
            )
        )
        used.add(identifier)

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
        append_preset(identifier, item)

    for item in rendered_items:
        identifier = str(item.get("identifier", "") or "")
        if identifier and identifier not in used:
            append_preset(identifier, item)

    if "mofox_system" not in used:
        output.extend(system_block)
    if "mofox_tool" not in used:
        output.extend(function_block)
    if "mofox_user" not in used:
        output.extend(convo_block)

    payloads[:] = output
    return _demote_invalid_assistant_payloads(payloads, preset_names)


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

        demoted: list[str] = []
        novel_result: dict[str, Any] | None = None
        if config.plugin.inject_setvar:
            service = TavernDataService(plugin=self.plugin)
            if hasattr(service, "render_setvar_payloads") and hasattr(
                service, "resolve_mofox_order"
            ):
                # 先取当前小说段落并推进进度，再让渲染阶段用它的内容填充变量。
                novel_result = _inject_novel_segment(config, params, service)
                if novel_result is not None and config.plugin.debug_log:
                    logger.info(
                        "小说注入：file={} 段={}~{} / {} finished={} looped={}".format(
                            novel_result.get("file"),
                            novel_result.get("start"),
                            novel_result.get("end"),
                            novel_result.get("total"),
                            novel_result.get("finished"),
                            novel_result.get("looped"),
                        )
                    )
                demoted = _inject_ordered_setvar_payloads(
                    payloads,
                    service,
                    config=config,
                    novel=novel_result,
                )
                if demoted and config.plugin.debug_log:
                    logger.info(
                        "以下 assistant 预设条目因顺序非法被降级为 system："
                        + ", ".join(demoted)
                    )
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
                "主回复模型请求已处理：request_name={} payloads={} roles={}".format(
                    params.get("request_name"),
                    len(payloads),
                    ",".join(
                        str(getattr(payload, "role", "")) for payload in payloads
                    ),
                )
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
                            "主回复模型结果已处理：request_name={}".format(
                                params.get("request_name")
                            )
                        )

        return EventDecision.SUCCESS, params
