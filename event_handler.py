"""只作用于主回复模型请求与结果的事件处理器。"""

from __future__ import annotations

import hashlib
import json
from typing import Any

from src.app.plugin_system.api.log_api import get_logger
from src.app.plugin_system.api.stream_api import get_stream_info
from src.app.plugin_system.base import BaseEventHandler
from src.app.plugin_system.types import ROLE, LLMPayload, Text, ToolCall, ToolResult
from src.core.components.types import EventType
from src.kernel.event import EventDecision

from .config import TavernRegexConfig
from .engine import apply_rules
from .novel_store import NEW_INPUT_ENTRY_ID
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


def _has_setvar_marker(payload: Any) -> bool:
    """判断单条 payload 是否带着本插件注入预设时打的标记。"""
    return any(
        isinstance(item, Text) and _SETVAR_MARKER in item.text
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


def _render_tool_call(part: Any) -> str:
    """把一次工具调用渲染成一行文本。"""
    name = str(getattr(part, "name", "") or "")
    args = getattr(part, "args", None)
    if isinstance(args, dict):
        try:
            args_text = json.dumps(args, ensure_ascii=False)
        except (TypeError, ValueError):
            args_text = str(args)
    else:
        args_text = str(args)
    return f"调用工具 {name}({args_text})"


def _render_conversation_block(payloads: list[Any]) -> str:
    """把对话块（历史 + 上轮回复 + 工具调用 + 本轮新输入）渲染成文本。

    供预设条目里的 ``{{mofox_conversation}}`` 使用，让预设条目也能拿到
    每轮追加的回复与工具调用记录。
    """
    lines: list[str] = []
    for payload in payloads:
        role = str(getattr(payload, "role", ""))
        if role == str(ROLE.USER):
            label = "用户"
        elif role == str(ROLE.ASSISTANT):
            label = "助手"
        elif role == str(ROLE.TOOL_RESULT):
            label = "工具结果"
        elif role == str(ROLE.TOOL):
            continue
        else:
            label = role or "未知"

        for part in getattr(payload, "content", []) or []:
            if isinstance(part, Text):
                if part.text.strip():
                    lines.append(f"[{label}] {part.text}")
            elif isinstance(part, ToolCall):
                lines.append(f"[{label}] {_render_tool_call(part)}")
            elif isinstance(part, ToolResult):
                lines.append(f"[工具结果] {part.to_text()}")
            else:
                # ReasoningText 等其它文本型内容
                text = getattr(part, "text", None)
                if isinstance(text, str) and text.strip():
                    lines.append(f"[{label}·思考] {text}")
    return "\n\n".join(lines)


def _has_tool_content(payload: Any) -> bool:
    """payload 里是否含有工具调用/工具结果。"""
    for part in getattr(payload, "content", []) or []:
        if isinstance(part, (ToolCall, ToolResult)):
            return True
    return False


def _merge_adjacent_same_role(payloads: list[Any]) -> None:
    """把相邻的纯文本 user payload 合并，行为与 MoFox 的 add_payload 一致。

    「历史 + 本轮新输入」被排到一起时会重新合成一条 user 消息。
    只合并 user：assistant 不合并，避免把预填充并进上轮回复。
    """
    merged: list[Any] = []
    for payload in payloads:
        role = str(getattr(payload, "role", ""))
        content = getattr(payload, "content", None)
        if (
            role == str(ROLE.USER)
            and merged
            and str(getattr(merged[-1], "role", "")) == role
            and isinstance(content, list)
            and isinstance(getattr(merged[-1], "content", None), list)
            and not _has_tool_content(payload)
            and not _has_tool_content(merged[-1])
        ):
            merged[-1].content.extend(content)
            continue
        merged.append(payload)
    payloads[:] = merged


def _split_before_last_user(
    convo_block: list[Any],
) -> tuple[list[Any], list[Any], list[Any]]:
    """把对话块拆成「历史」「本轮新输入」「新输入之后的收尾」三段。

    MoFox 的 payload 顺序是：历史(user) → 上轮回复(assistant) → 工具结果 →
    __SUSPEND__(assistant) → 本轮新输入(user)。最后一条 user 就是本轮新输入；
    它**之前**的「上轮回复 + 工具调用 + 工具结果 + __SUSPEND__」都属于历史
    侧，必须跟着历史一起输出，否则会被夹在中间的预填充条目（ass / jailbreak）
    隔开，跑到请求末尾去。

    所以这里以最后一条 user 为界：

    * 历史 = 最后一条 user 之前的**全部** payload（含 assistant / tool_result）
    * 本轮新输入 = 最后一条 user
    * 收尾 = 最后一条 user 之后剩余的 payload

    特殊情形：第一轮请求里 MoFox 把「历史 + 本轮新消息」拼进同一条 user
    payload（``build_user_prompt`` 的 history + unreads），这时整块都算本轮
    新输入，历史部分为空——返回 ``([], 整块, [])``。

    找不到 user 时返回 ``(整个对话块, [], [])``。
    """
    last_user = -1
    for index, payload in enumerate(convo_block):
        if str(getattr(payload, "role", "")) == str(ROLE.USER):
            last_user = index
    if last_user < 0:
        return list(convo_block), [], []

    if last_user == 0:
        # 只有一条 user（历史与本轮新消息已合并）→ 整块都算本轮新输入。
        return [], list(convo_block), []

    return (
        list(convo_block[:last_user]),
        [convo_block[last_user]],
        list(convo_block[last_user + 1 :]),
    )


def _strip_setvar_marker(payload: Any) -> str:
    """取出一条已注入预设 payload 的原始内容（去掉标记前缀）。"""
    for part in getattr(payload, "content", []) or []:
        if isinstance(part, Text) and _SETVAR_MARKER in part.text:
            text = part.text
            index = text.find(_SETVAR_MARKER)
            return text[index + len(_SETVAR_MARKER) :].lstrip("\n")
    return ""


def _split_injected_presets(
    convo_block: list[Any],
) -> tuple[list[Any], list[tuple[str, Any]]]:
    """把「已经注入过预设」的对话块拆成纯对话部分与预设部分。

    工具调用后的二次请求里，上一轮注入的预设条目仍然留在 payload 里。这时
    不能重复注入，但**仍然要按顺序表重排**，否则上轮回复与工具结果会被挤在
    预设后面、预填充也就落不到末尾。

    只有「纯文本 + 带标记」的 payload 才算已注入预设：带工具调用/工具结果的
    payload 属于真实对话（例如带 tool_call 的 assistant 回复），必须留在
    对话块里，否则工具调用与工具结果会被拆散。

    返回 ``(纯对话 payload 列表, [(内容, payload), ...])``。
    """
    convo: list[Any] = []
    presets: list[tuple[str, Any]] = []
    for payload in convo_block:
        if _has_setvar_marker(payload) and not _has_tool_content(payload):
            presets.append((_strip_setvar_marker(payload), payload))
        else:
            convo.append(payload)
    return convo, presets


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

    本函数是**幂等**的：如果 payload 里已经带着上次注入的 ``setvar`` 标记
    （工具调用后的二次请求就是这样），就只按顺序表重排已有条目，不再重复
    注入。否则先渲染并注入新条目，再重排。
    """
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

    # 对话块文本（历史 + 上轮回复 + 工具调用 + 本轮新输入），
    # 供预设条目里的 {{mofox_conversation}} 使用。
    _, _, convo_block_for_text = _split_mofox_payloads(payloads)
    conversation_text = _render_conversation_block(convo_block_for_text)

    rendered_items = service.render_setvar_payloads(
        seed_variables=seed,
        include_novel=include_novel,
        conversation=conversation_text,
    )
    order_ids = service.resolve_mofox_order(service.load_setvar_payload())
    by_identifier = {
        str(item.get("identifier", "") or ""): item for item in rendered_items
    }

    system_block, function_block, convo_block = _split_mofox_payloads(payloads)
    # 二次请求：对话块里混着上一轮注入的预设，先剥掉它们（带工具调用的除外）。
    convo_block, injected = _split_injected_presets(convo_block)
    # 内容 → 已注入的预设 payload，按顺序消费，避免同名内容互相顶掉。
    injected_by_content: dict[str, list[Any]] = {}
    for content_text, payload in injected:
        injected_by_content.setdefault(content_text, []).append(payload)

    # 对话块拆成三段，各自按顺序表里的位置插入：
    #   mofox_user      → 历史（此前发生的事情 + 上轮回复 + 工具调用 + 工具结果）
    #   mofox_new_input → 本轮新输入
    #   after_part      → 新输入之后的收尾（如 __SUSPEND__），紧跟对话块
    history_part, tail_part, after_part = _split_before_last_user(convo_block)
    output: list[Any] = []
    used: set[str] = set()
    preset_names: dict[int, str] = {}
    # 收尾 payload 要贴在对话块后面，因此记住「新输入」的位置；没有新输入时
    # 退回历史的位置。
    new_input_at: int | None = None
    history_at: int | None = None

    def append_preset(identifier: str, item: dict[str, Any]) -> None:
        content_text = str(item.get("content", ""))
        preset_names[len(output)] = str(item.get("name", "") or identifier)
        pending = injected_by_content.get(content_text)
        if pending:
            output.append(pending.pop(0))
        else:
            output.append(
                _build_setvar_payload(
                    _tavern_role_to_role(item.get("role")),
                    content_text,
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
            output.extend(history_part)
            history_at = len(output)
            used.add(identifier)
            continue
        if identifier == NEW_INPUT_ENTRY_ID:
            output.extend(tail_part)
            new_input_at = len(output)
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

    # 顺序表里缺失的固定块补到末尾（与旧行为一致）。
    if "mofox_system" not in used:
        output.extend(system_block)
    if "mofox_tool" not in used:
        output.extend(function_block)
    if "mofox_user" not in used:
        output.extend(history_part)
        history_at = len(output)
    if NEW_INPUT_ENTRY_ID not in used:
        output.extend(tail_part)
        new_input_at = len(output)

    # 新输入之后的收尾（__SUSPEND__ 等）永远紧贴对话块：有新输入就贴在新输入
    # 之后，否则贴在历史之后。避免它被甩到请求最后、把预填充隔开。
    if after_part:
        anchor = new_input_at if new_input_at is not None else history_at
        if anchor is None:
            anchor = len(output)
        output[anchor:anchor] = after_part

    payloads[:] = output
    _merge_adjacent_same_role(payloads)
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
                    ",".join(str(getattr(payload, "role", "")) for payload in payloads),
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
