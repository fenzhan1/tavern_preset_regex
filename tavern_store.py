"""直接读写 ``data/tavern_preset_regex`` 下的 SillyTavern 兼容文件。"""

from __future__ import annotations

import json
import random
import re
import string
import uuid
from pathlib import Path
from typing import Any

from src.app.plugin_system.api.log_api import get_logger
from src.core.config import get_core_config

from .config import RuleSection, TavernRegexConfig
from .novel_store import (
    NOVEL_ENTRY_CONTENT,
    NOVEL_ENTRY_ID,
    NOVEL_ENTRY_NAME,
    NOVEL_RUNTIME_KEYS,
    NovelRuntimeConfig,
    NovelService,
    normalize_novel_config,
    segment_title,
)

logger = get_logger("tavern_preset_regex.tavern_store")

_WORKSPACE_ROOT = Path(__file__).resolve().parents[2]
if not (_WORKSPACE_ROOT / "config" / "core.toml").is_file():
    _WORKSPACE_ROOT = Path.cwd()
DEFAULT_TAVERN_DIR = _WORKSPACE_ROOT / "data" / "tavern_preset_regex"

_REGEX_CACHE: dict[Path, tuple[float, list[dict[str, Any]]]] = {}


def _read_json(path: Path, default: Any) -> Any:
    """读取 JSON 文件；文件不存在时创建默认内容。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.is_file():
        path.write_text(
            json.dumps(default, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        return default
    return json.loads(path.read_text(encoding="utf-8-sig"))


def _write_json(path: Path, payload: Any) -> None:
    """原子地写入 JSON 文件，保留可读缩进。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    temp.replace(path)


def _empty_regex_payload() -> list[dict[str, Any]]:
    return []


def _empty_setvar_payload() -> dict[str, Any]:
    return {
        "prompts": [],
        "prompt_order": [],
    }


# SillyTavern 的插入位标记（聊天记录、角色卡、世界书等），
# 这些内容由 MoFox 自身构建，导入时直接跳过
_MARKER_IDENTIFIERS = frozenset(
    (
        "chatHistory",
        "dialogueExamples",
        "worldInfoBefore",
        "worldInfoAfter",
        "charDescription",
        "charPersonality",
        "scenario",
        "personaDescription",
    )
)

# 与 MoFox 主回复指令重复的酒馆默认系统提示词，首次导入时默认停用
_MOFOX_REDUNDANT_IDENTIFIERS = frozenset(("main", "nsfw"))

# MoFox 主回复请求里永远存在的三块固定内容。
_MOFOX_FIXED_PROMPT_IDS = ("mofox_system", "mofox_tool", "mofox_user")

# 只读条目的完整顺序：三块固定内容 + 一条「📖小说当前段落」动态条目。
# 它们在 WebUI 中作为只读条目展示，只允许调整相对顺序，不允许编辑名称、角色或内容。
_MOFOX_FIXED_ORDER_IDS = (
    "mofox_system",
    "mofox_tool",
    "mofox_user",
    NOVEL_ENTRY_ID,
)

_MOFOX_FIXED_PROMPTS: dict[str, dict[str, Any]] = {
    "mofox_system": {
        "identifier": "mofox_system",
        "name": "MoFox 系统提示词",
        "role": "system",
        "content": "由 MoFox 主回复 chatter 自动构建，不可编辑。",
        "enabled": True,
        "fixed": True,
    },
    "mofox_tool": {
        "identifier": "mofox_tool",
        "name": "MoFox Tool",
        "role": "function",
        "content": "由 MoFox 可用工具集自动构建，不可编辑。",
        "enabled": True,
        "fixed": True,
    },
    "mofox_user": {
        "identifier": "mofox_user",
        "name": "MoFox 用户上下文",
        "role": "user",
        "content": "由 MoFox 历史消息与当前用户输入自动构建，不可编辑。",
        "enabled": True,
        "fixed": True,
    },
}


def _normalize_tavern_pattern(pattern: str) -> tuple[str, str]:
    """把 JavaScript 风格的 ``/pattern/flags`` 转成 Python ``re`` 可用的内容。"""
    pattern = (pattern or "").strip()
    if len(pattern) >= 2 and pattern.startswith("/"):
        end = pattern.rfind("/")
        if end > 0:
            body = pattern[1:end].replace("\\/", "/")
            return body, pattern[end + 1 :]
    return pattern, ""


def _target_for_regex_entry(entry: dict[str, Any]) -> str:
    """按 SillyTavern 的 markdownOnly / promptOnly 和 placement 映射目标。"""
    placement: tuple[int, ...] = ()
    raw_placement = entry.get("placement")
    if isinstance(raw_placement, list):
        placement = tuple(
            int(value)
            for value in raw_placement
            if isinstance(value, int) or str(value).isdigit()
        )

    markdown_only = bool(entry.get("markdownOnly", False))
    prompt_only = bool(entry.get("promptOnly", False))

    if markdown_only and prompt_only:
        return "both"
    if markdown_only:
        return "output"
    if prompt_only:
        return "input"
    if 0 in placement and 1 in placement:
        return "both"
    if 0 in placement:
        return "input"
    if 1 in placement:
        return "output"
    return "both"


def _convert_replacement(value: str) -> str:
    """把酒馆的 ``$1`` / ``${name}`` 替换写法转成 Python ``re`` 语法。"""
    value = re.sub(r"\$(\d+)", lambda match: "\\" + match.group(1), value)
    value = re.sub(r"\$\{(\w+)\}", lambda match: "\\g<" + match.group(1) + ">", value)
    return value


def _entry_to_rule(entry: dict[str, Any]) -> RuleSection | None:
    """把一条 SillyTavern regex script 转换为插件规则。"""
    pattern, flags = _normalize_tavern_pattern(
        str(entry.get("findRegex", "") or entry.get("find_regex", "") or "")
    )
    if not pattern:
        return None

    replacement = _convert_replacement(
        str(entry.get("replaceString", "") or entry.get("replace_string", "") or "")
    )
    name = str(
        entry.get("scriptName", "")
        or entry.get("script_name", "")
        or entry.get("id", "")
        or "未命名正则"
    )
    target = _target_for_regex_entry(entry)
    return RuleSection(
        name=name,
        enabled=not bool(entry.get("disabled", False)),
        target=target,  # type: ignore[arg-type]
        pattern=pattern,
        replacement=replacement,
        flags=flags,
        description=f"data/tavern_preset_regex/regex.json #{entry.get('id', '')}".strip(),
    )


def _render_tavern_macros(
    content: str,
    variables: dict[str, str],
    *,
    bot_name: str = "",
    user_name: str = "用户",
) -> str:
    """处理酒馆预设中常用的变量与随机宏。"""

    def _setvar(match: re.Match[str]) -> str:
        name = match.group(1).strip()
        value = match.group(2).strip("\n").strip()
        variables[name] = value
        return ""

    def _addvar(match: re.Match[str]) -> str:
        name = match.group(1).strip()
        value = match.group(2).strip("\n").strip()
        if value:
            variables[name] = (variables.get(name, "") + "\n" + value).strip()
        return ""

    content = re.sub(
        r"\{\{setvar::([^{}:]+)::([\s\S]*?)\}\}",
        _setvar,
        content,
    )
    content = re.sub(
        r"\{\{addvar::([^{}:]+)::([\s\S]*?)\}\}",
        _addvar,
        content,
    )
    content = re.sub(
        r"\{\{getvar::([^{}:]+)\}\}",
        lambda match: variables.get(match.group(1).strip(), ""),
        content,
    )
    content = re.sub(
        r"\{\{random_string_(\d+)\}\}",
        lambda match: "".join(
            random.choices(string.ascii_letters + string.digits, k=int(match.group(1)))
        ),
        content,
    )
    content = re.sub(
        r"\{\{random_number_(\d+)\}\}",
        lambda match: "".join(random.choices(string.digits, k=int(match.group(1)))),
        content,
    )
    content = content.replace("{{trim}}", "").replace("{{lastUserMessage}}", "")
    content = content.replace("{{char}}", bot_name).replace("{{user}}", user_name)
    return content.strip()


def _first_order(payload: dict[str, Any]) -> dict[str, Any]:
    """获取第一组 prompt_order，没有时创建一个。"""
    orders = payload.setdefault("prompt_order", [])
    if not isinstance(orders, list):
        orders = []
        payload["prompt_order"] = orders
    if not orders:
        orders.append({"character_id": 0, "order": []})
    if not isinstance(orders[0], dict):
        orders.insert(0, {"character_id": 0, "order": []})
    return orders[0]


def _prompt_matches(prompt: dict[str, Any], key: str, index: int) -> bool:
    """判断 prompt 是否匹配 id、名称或从 1 开始的序号。"""
    if key == str(prompt.get("identifier", "")):
        return True
    name = str(prompt.get("name", ""))
    if name == key:
        return True
    if key and key in name:
        return True
    if key.isdigit() and int(key) == index:
        return True
    return False


def _first_order_entries(payload: dict[str, Any]) -> list[tuple[str, bool]]:
    """解析第一组非空 prompt_order，返回 (identifier, enabled) 列表。"""
    raw_order = payload.get("prompt_order")
    if not isinstance(raw_order, list):
        return []
    entries: list[tuple[str, bool]] = []
    for group in raw_order:
        if not isinstance(group, dict):
            continue
        raw_items = group.get("order")
        if not isinstance(raw_items, list):
            continue
        for item in raw_items:
            if not isinstance(item, dict):
                continue
            identifier = str(item.get("identifier", ""))
            if not identifier:
                continue
            entries.append((identifier, bool(item.get("enabled", True))))
        if entries:
            break
    return entries


def _canonical_prompt_sequence(payload: dict[str, Any]) -> list[dict[str, Any]]:
    """按 prompt_order 排列出全部提示词，未收录的按原数组顺序追加。"""
    prompts = [item for item in payload.get("prompts", []) if isinstance(item, dict)]

    prompt_by_id: dict[str, dict[str, Any]] = {}
    for prompt in prompts:
        identifier = str(prompt.get("identifier", ""))
        if identifier:
            prompt_by_id.setdefault(identifier, prompt)

    sequence: list[dict[str, Any]] = []
    seen: set[str] = set()
    for identifier, _enabled in _first_order_entries(payload):
        prompt = prompt_by_id.get(identifier)
        if prompt is None or identifier in seen:
            continue
        seen.add(identifier)
        sequence.append(prompt)

    for prompt in prompts:
        identifier = str(prompt.get("identifier", ""))
        if identifier and identifier in seen:
            continue
        sequence.append(prompt)
    return sequence


def _sort_prompts_by_order(
    prompts: list[dict[str, Any]],
    order_enabled: dict[str, bool],
) -> list[dict[str, Any]]:
    """按 prompt_order 的先后排列提示词，未收录的保持原顺序追加在后。"""
    position = {identifier: index for index, identifier in enumerate(order_enabled)}
    ranked = sorted(
        (
            (
                prompt,
                position.get(
                    str(prompt.get("identifier", "") or ""),
                    len(position),
                ),
            )
            for prompt in prompts
        ),
        key=lambda pair: pair[1],
    )
    return [prompt for prompt, _ in ranked]


def _prompts_in_mofox_order(payload: dict[str, Any]) -> list[dict[str, Any]]:
    """按统一顺序返回提示词；旧数据无 mofox_order 时退化为 prompt_order 顺序。"""
    order = TavernDataService._normalized_mofox_order(payload, append_missing=True)
    fixed_ids = set(_MOFOX_FIXED_ORDER_IDS)
    prompt_by_id = {
        str(prompt.get("identifier", "") or ""): prompt
        for prompt in payload.get("prompts", [])
        if isinstance(prompt, dict) and prompt.get("identifier")
    }

    sequence: list[dict[str, Any]] = []
    seen: set[str] = set()
    for identifier in order:
        if identifier in fixed_ids:
            continue
        prompt = prompt_by_id.get(identifier)
        if prompt is None or identifier in seen:
            continue
        seen.add(identifier)
        sequence.append(prompt)

    for prompt in _canonical_prompt_sequence(payload):
        identifier = str(prompt.get("identifier", "") or "")
        if identifier and identifier not in seen:
            seen.add(identifier)
            sequence.append(prompt)
    return sequence


def _ordered_enabled_prompts(
    payload: dict[str, Any],
    extra_items: list[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    """按统一顺序取出启用的非空提示词。

    ``extra_items`` 用于注入不属于 setvar.json 的虚拟条目（如「📖小说当前段落」），
    它们按 ``mofox_order`` 的位置参与渲染。
    """
    order_enabled = dict(_first_order_entries(payload))
    extras = {
        str(item.get("identifier", "")): item
        for item in (extra_items or [])
        if item.get("identifier")
    }
    prompt_by_id = {
        str(prompt.get("identifier", "") or ""): prompt
        for prompt in _prompts_in_mofox_order(payload)
        if prompt.get("identifier")
    }
    fixed_ids = set(_MOFOX_FIXED_ORDER_IDS)
    enabled: list[dict[str, Any]] = []
    used: set[str] = set()

    for identifier in TavernDataService._normalized_mofox_order(
        payload, append_missing=True
    ):
        if identifier in fixed_ids or identifier in used:
            continue
        extra = extras.get(identifier)
        if extra is not None:
            used.add(identifier)
            if extra.get("enabled") and str(extra.get("content", "")).strip():
                enabled.append(extra)
            continue
        prompt = prompt_by_id.get(identifier)
        if prompt is None:
            continue
        used.add(identifier)
        effective = order_enabled.get(identifier, bool(prompt.get("enabled", True)))
        if effective and prompt.get("content"):
            enabled.append(prompt)

    for identifier, prompt in prompt_by_id.items():
        if identifier in used:
            continue
        effective = order_enabled.get(identifier, bool(prompt.get("enabled", True)))
        if effective and prompt.get("content"):
            enabled.append(prompt)

    for identifier, extra in extras.items():
        if identifier in used:
            continue
        if extra.get("enabled") and str(extra.get("content", "")).strip():
            enabled.append(extra)

    return enabled


class TavernDataService:
    """``data/tavern_preset_regex`` 下 setvar 预设与 regex 规则的读写、编辑和应用。"""

    def __init__(
        self,
        tavern_dir: str | Path | None = None,
        plugin: Any = None,
    ) -> None:
        self.tavern_dir = self._resolve_tavern_dir(tavern_dir, plugin)
        self.setvar_path = self.tavern_dir / "setvar.json"
        self.regex_path = self.tavern_dir / "regex.json"
        self.novel_dir = self.tavern_dir / "novel"
        self.plugin = plugin

    @staticmethod
    def _resolve_tavern_dir(
        tavern_dir: str | Path | None,
        plugin: Any,
    ) -> Path:
        if tavern_dir is not None:
            return Path(tavern_dir)

        config = getattr(plugin, "config", None)
        if isinstance(config, TavernRegexConfig):
            raw = getattr(config.plugin, "data_dir", "") or ""
            if raw:
                path = Path(raw)
                if not path.is_absolute():
                    path = _WORKSPACE_ROOT / path
                return path
        return DEFAULT_TAVERN_DIR

    def ensure_dir(self) -> None:
        self.tavern_dir.mkdir(parents=True, exist_ok=True)

    # ----- regex 读写 -----
    def load_regex_entries(self, *, use_cache: bool = True) -> list[dict[str, Any]]:
        """读取 regex.json 中的规则列表。"""
        self.ensure_dir()
        if use_cache:
            try:
                mtime = self.regex_path.stat().st_mtime
            except FileNotFoundError:
                mtime = 0.0
            cached = _REGEX_CACHE.get(self.regex_path)
            if cached is not None and cached[0] == mtime:
                return cached[1]

        payload = _read_json(self.regex_path, _empty_regex_payload())
        if not isinstance(payload, list):
            raise ValueError("data/tavern_preset_regex/regex.json 顶层必须是数组")
        entries = [item for item in payload if isinstance(item, dict)]
        if use_cache:
            try:
                mtime = self.regex_path.stat().st_mtime
            except FileNotFoundError:
                mtime = 0.0
            _REGEX_CACHE[self.regex_path] = (mtime, entries)
        return entries

    def save_regex_entries(self, entries: list[dict[str, Any]]) -> None:
        """保存 regex.json。"""
        self.ensure_dir()
        _write_json(self.regex_path, entries)
        _REGEX_CACHE.pop(self.regex_path, None)

    def list_rules(self) -> list[RuleSection]:
        """把 regex.json 中的规则转换为插件规则列表。"""
        rules: list[RuleSection] = []
        for entry in self.load_regex_entries():
            rule = _entry_to_rule(entry)
            if rule is not None:
                rules.append(rule)
        return rules

    def find_regex_entry(self, key: str) -> tuple[dict[str, Any], int]:
        """按 id、scriptName 或从 1 开始的序号查找规则。"""
        entries = self.load_regex_entries()
        return self._find_regex_entry_in(entries, key)

    @staticmethod
    def _find_regex_entry_in(
        entries: list[dict[str, Any]],
        key: str,
    ) -> tuple[dict[str, Any], int]:
        """在给定列表中查找规则。"""
        for index, entry in enumerate(entries, start=1):
            if key == str(entry.get("id", "")):
                return entry, index
            if key == str(entry.get("scriptName", "")):
                return entry, index
            if key and key in str(entry.get("scriptName", "")):
                return entry, index
            if key.isdigit() and int(key) == index:
                return entry, index
        raise ValueError(f"未找到正则规则: {key}")

    def set_regex_enabled(self, key: str, enabled: bool) -> dict[str, Any]:
        """启用/禁用一条规则并保存。"""
        entries = self.load_regex_entries()
        _, index = self._find_regex_entry_in(entries, key)
        entries[index - 1]["disabled"] = not enabled
        self.save_regex_entries(entries)
        return entries[index - 1]

    def update_regex_entry(self, key: str, **fields: Any) -> dict[str, Any]:
        """更新一条规则的指定字段并保存。"""
        entries = self.load_regex_entries()
        _, index = self._find_regex_entry_in(entries, key)
        if not fields:
            raise ValueError("没有提供要更新的字段")
        entry = entries[index - 1]
        for field, value in fields.items():
            entry[field] = value
        self.save_regex_entries(entries)
        return entries[index - 1]

    # ----- setvar 预设读写与应用 -----
    def load_setvar_payload(self) -> dict[str, Any]:
        """读取 setvar.json 的原始对话补全预设。"""
        self.ensure_dir()
        payload = _read_json(self.setvar_path, _empty_setvar_payload())
        if not isinstance(payload, dict):
            raise ValueError("data/tavern_preset_regex/setvar.json 顶层必须是对象")
        payload.setdefault("prompts", [])
        payload.setdefault("prompt_order", [])
        payload.setdefault("mofox_order", [])
        return payload

    def save_setvar_payload(self, payload: dict[str, Any]) -> None:
        """保存 setvar.json。"""
        self.ensure_dir()
        _write_json(self.setvar_path, payload)

    @staticmethod
    def fixed_prompt_items() -> list[dict[str, Any]]:
        """返回 WebUI 展示的三个 MoFox 固定条目（不含小说动态条目）。"""
        return [dict(_MOFOX_FIXED_PROMPTS[key]) for key in _MOFOX_FIXED_PROMPT_IDS]

    def novel_prompt_item(self) -> dict[str, Any]:
        """返回「📖小说当前段落」虚拟条目。

        内容固定为 ``{{getvar::变量名}}``，由小说进度在渲染时填充，因此条目本身
        只需要维护启用状态与在 ``mofox_order`` 中的位置。
        """
        try:
            settings = self.novel_settings()
        except Exception:  # noqa: BLE001 - 配置读取异常时退回默认值
            settings = {}
        variable = str(settings.get("variable_name") or "current_chapter")
        return {
            "identifier": NOVEL_ENTRY_ID,
            "name": NOVEL_ENTRY_NAME,
            "role": str(settings.get("role") or "system"),
            "content": NOVEL_ENTRY_CONTENT.replace("current_chapter", variable),
            "enabled": bool(settings.get("entry_enabled", True)),
            "fixed": True,
            "novel": True,
        }

    def readonly_prompt_items(self) -> list[dict[str, Any]]:
        """返回所有只读条目：三条 MoFox 固定块 + 小说动态条目。"""
        return [*self.fixed_prompt_items(), self.novel_prompt_item()]

    @staticmethod
    def _readonly_identifiers() -> set[str]:
        """只读条目的 identifier 集合，用于保存时过滤。"""
        return {*_MOFOX_FIXED_ORDER_IDS}

    @staticmethod
    def _prompt_identifiers(payload: dict[str, Any]) -> list[str]:
        """提取 payload 中全部可排序的酒馆预设 identifier。"""
        return [
            str(prompt.get("identifier", "") or "")
            for prompt in payload.get("prompts", [])
            if isinstance(prompt, dict) and prompt.get("identifier")
        ]

    @staticmethod
    def _normalized_mofox_order(
        payload: dict[str, Any],
        *,
        append_missing: bool = True,
    ) -> list[str]:
        """规范化 MoFox 固定条目与酒馆预设的统一顺序。

        已有的 ``mofox_order`` 优先；固定条目缺失时按默认位置补齐，未收录的
        酒馆预设按现有顺序追加到末尾。
        """
        raw_order = payload.get("mofox_order")
        order: list[str] = []
        if isinstance(raw_order, list):
            seen: set[str] = set()
            for item in raw_order:
                identifier = str(item).strip()
                # 兼容早期版本使用过的 mofox_function 标识。
                if identifier == "mofox_function":
                    identifier = "mofox_tool"
                if identifier and identifier not in seen:
                    order.append(identifier)
                    seen.add(identifier)

        prompt_ids = TavernDataService._prompt_identifiers(payload)
        if not order:
            order = [
                "mofox_system",
                *prompt_ids,
                "mofox_tool",
                "mofox_user",
            ]

        if append_missing:
            existing = set(order)
            default_fixed = list(_MOFOX_FIXED_ORDER_IDS)
            for identifier in default_fixed:
                if identifier not in existing:
                    if identifier == "mofox_system":
                        order.insert(0, identifier)
                    else:
                        order.append(identifier)
                    existing.add(identifier)
            for identifier in prompt_ids:
                if identifier not in existing:
                    order.append(identifier)
                    existing.add(identifier)

        return order

    def resolve_mofox_order(self, payload: dict[str, Any] | None = None) -> list[str]:
        """返回当前统一顺序，供请求组装与 WebUI 使用。"""
        payload = payload or self.load_setvar_payload()
        return self._normalized_mofox_order(payload, append_missing=True)

    def save_mofox_order(self, order: list[str]) -> None:
        """仅保存统一顺序，不改动 prompts 内容。"""
        payload = self.load_setvar_payload()
        payload["mofox_order"] = [
            str(item).strip() for item in order if str(item).strip()
        ]
        self.save_setvar_payload(payload)

    # ----- 小说分段注入 -----
    def novel_service(self) -> NovelService:
        """返回绑定到 ``novel/`` 目录的小说服务。"""
        return NovelService(self.novel_dir)

    def novel_config(self) -> Any:
        """返回插件 config.toml 里的小说配置段（缺失时返回 None）。"""
        config = getattr(self.plugin, "config", None)
        return getattr(config, "novel", None)

    def novel_settings(self) -> dict[str, Any]:
        """合并 config.toml 的 ``[novel]`` 与 ``novel/config.json`` 的运行时设置。"""
        novel = self.novel_config()
        base: dict[str, Any] = {}
        if novel is not None:
            base = {
                key: getattr(novel, key)
                for key in NOVEL_RUNTIME_KEYS
                if hasattr(novel, key)
            }
        runtime = NovelRuntimeConfig(self.novel_dir).load()
        base.update(runtime)
        return normalize_novel_config(base)

    def save_novel_settings(self, updates: dict[str, Any]) -> dict[str, Any]:
        """保存运行时小说设置到 ``novel/config.json``。"""
        return NovelRuntimeConfig(self.novel_dir).save(updates)

    def resolve_novel_file(self, name: str) -> Path:
        """校验小说文件名并返回路径。"""
        return self.novel_service().resolve_file(name)

    def active_novel_file(self) -> str:
        """当前小说文件名：设置优先，未配置时取目录里第一个。"""
        settings = self.novel_settings()
        configured = str(settings.get("file", "") or "").strip()
        if configured:
            return configured
        files = self.novel_service().list_files()
        return str(files[0]["name"]) if files else ""

    def _load_novel_segments(
        self,
        active: str,
        settings: dict[str, Any],
    ) -> dict[str, Any]:
        """按当前设置读取并切分小说。"""
        return self.novel_service().load_segments(
            active,
            mode=str(settings.get("split_mode", "auto")),
            char_size=int(settings.get("char_size", 10000)),
            lines_per_segment=int(settings.get("lines_per_segment", 60)),
            chapter_pattern=str(settings.get("chapter_pattern", "")),
        )

    def novel_state(self, stream_id: str | None = None) -> dict[str, Any]:
        """返回小说功能完整状态，供 WebUI 与命令使用。"""
        service = self.novel_service()
        files = service.list_files()
        active = self.active_novel_file()
        config_payload = self.novel_settings()

        state: dict[str, Any] = {
            "dir": str(self.novel_dir),
            "files": files,
            "active_file": active,
            "config": config_payload,
            "segments": [],
            "segment_titles": [],
            "segment_chars": [],
            "index": 0,
            "total": 0,
            "mode": "",
            "label": "",
            "chars": 0,
            "error": "",
        }
        if not active:
            return state

        try:
            loaded = self._load_novel_segments(active, config_payload)
        except ValueError as exc:
            state["error"] = str(exc)
            return state

        segments: list[str] = list(loaded["segments"])
        state["segments"] = segments
        state["segment_titles"] = [segment_title(item) for item in segments]
        state["segment_chars"] = [len(item) for item in segments]
        state["total"] = len(segments)
        state["mode"] = loaded["mode"]
        state["label"] = loaded["label"]
        state["chars"] = loaded["chars"]
        state["index"] = service.get_index(active, stream_id)
        return state

    def advance_novel(
        self,
        stream_id: str | None = None,
    ) -> dict[str, Any]:
        """手动推进小说进度，返回本次注入的段落信息。"""
        settings = self.novel_settings()
        service = self.novel_service()
        active = self.active_novel_file()
        if not active:
            raise ValueError("novel/ 目录下没有小说文件")

        loaded = self._load_novel_segments(active, settings)
        result = service.advance(
            active,
            list(loaded["segments"]),
            batch_size=int(settings.get("batch_size", 1)),
            loop=bool(settings.get("loop", False)),
            stream_id=stream_id,
        )
        result["file"] = active
        result["mode"] = loaded["mode"]
        result["label"] = loaded["label"]
        return result

    def jump_novel(
        self,
        index: int,
        stream_id: str | None = None,
    ) -> dict[str, Any]:
        """把进度定位到第 index 段（1 起始）并返回该段内容。"""
        settings = self.novel_settings()
        service = self.novel_service()
        active = self.active_novel_file()
        if not active:
            raise ValueError("novel/ 目录下没有小说文件")

        loaded = self._load_novel_segments(active, settings)
        segments = list(loaded["segments"])
        if not segments:
            raise ValueError("小说分段为空")
        target = max(1, min(int(index), len(segments)))
        service.set_index(active, target - 1, stream_id)
        return service.advance(
            active,
            segments,
            batch_size=int(settings.get("batch_size", 1)),
            loop=bool(settings.get("loop", False)),
            stream_id=stream_id,
        )

    def reset_novel(self, stream_id: str | None = None) -> None:
        """把小说进度重置到第一段。"""
        active = self.active_novel_file()
        if not active:
            raise ValueError("novel/ 目录下没有小说文件")
        self.novel_service().reset_progress(active, stream_id)

    @staticmethod
    def _bot_name() -> str:
        try:
            return get_core_config().personality.nickname
        except Exception:
            return ""

    def _render_prompts(
        self,
        payload: dict[str, Any] | None = None,
        *,
        seed_variables: dict[str, str] | None = None,
        include_novel: bool = False,
    ) -> tuple[str, dict[str, str], int]:
        """渲染 setvar.json 中启用的提示词。"""
        payload = payload or self.load_setvar_payload()
        variables: dict[str, str] = dict(seed_variables or {})
        blocks: list[str] = []
        count = 0
        extras = [self.novel_prompt_item()] if include_novel else None
        for prompt in _ordered_enabled_prompts(payload, extras):
            rendered = _render_tavern_macros(
                str(prompt.get("content", "")),
                variables,
                bot_name=self._bot_name(),
            )
            if not rendered:
                continue
            name = str(prompt.get("name") or "未命名")
            blocks.append(f"## {name}\n{rendered}")
            count += 1

        prompt_text = "\n\n".join(blocks)
        if prompt_text:
            prompt_text = f"# data/tavern_preset_regex/setvar.json\n\n{prompt_text}"
        return prompt_text, variables, count

    def render_setvar_prompt(self) -> str:
        """返回可直接注入主回复模型请求的 setvar 系统提示词。"""
        prompt_text, _, _ = self._render_prompts()
        return prompt_text

    def render_setvar_payloads(
        self,
        *,
        seed_variables: dict[str, str] | None = None,
        include_novel: bool = False,
    ) -> list[dict[str, Any]]:
        """按顺序渲染启用的酒馆预设条目。

        与 :meth:`render_setvar_prompt` 使用相同的变量解析顺序，但保留每条
        预设的 identifier、name、role 和渲染后的 content，便于请求组装时参与
        统一排序。

        ``seed_variables`` 用于预先塞入变量（例如小说当前段落），
        ``include_novel`` 决定是否把「📖小说当前段落」虚拟条目一起渲染。
        """
        payload = self.load_setvar_payload()
        variables: dict[str, str] = dict(seed_variables or {})
        rendered_items: list[dict[str, Any]] = []
        extras = [self.novel_prompt_item()] if include_novel else None

        for prompt in _ordered_enabled_prompts(payload, extras):
            content = _render_tavern_macros(
                str(prompt.get("content", "")),
                variables,
                bot_name=self._bot_name(),
            )
            if not content:
                continue
            rendered_items.append(
                {
                    "identifier": str(prompt.get("identifier", "") or ""),
                    "name": str(prompt.get("name") or "未命名"),
                    "role": str(prompt.get("role", "system") or "system"),
                    "content": content,
                }
            )

        return rendered_items

    def resolve_variables(self) -> dict[str, str]:
        """按 SillyTavern 顺序解析启用的 setvar/addvar 变量。"""
        _, variables, _ = self._render_prompts()
        return variables

    def get_variable(self, name: str) -> str:
        """读取一个已解析变量的当前值。"""
        name = name.strip()
        if not name:
            raise ValueError("变量名不能为空")
        return self.resolve_variables().get(name, "")

    def set_variable(self, name: str, value: str) -> str:
        """编辑或新增一个 setvar 变量定义，并保存回 setvar.json。"""
        name = name.strip()
        if not name:
            raise ValueError("变量名不能为空")

        payload = self.load_setvar_payload()
        prompts = payload["prompts"]
        if not isinstance(prompts, list):
            prompts = []
            payload["prompts"] = prompts

        setter_pattern = re.compile(
            r"\{\{setvar::" + re.escape(name) + r"::([\s\S]*?)\}\}"
        )
        replacement_text = f"{{{{setvar::{name}::\n{value}\n}}}}"

        for prompt in prompts:
            if not isinstance(prompt, dict):
                continue
            content = str(prompt.get("content", ""))
            if setter_pattern.search(content):
                prompt["content"] = setter_pattern.sub(
                    lambda _match: replacement_text,
                    content,
                )
                prompt["enabled"] = True
                self._sync_prompt_order(
                    payload,
                    str(prompt.get("identifier", "")),
                    True,
                )
                self.save_setvar_payload(payload)
                return value

        identifier = str(uuid.uuid4())
        prompts.append(
            {
                "identifier": identifier,
                "name": f"变量 {name}",
                "system_prompt": False,
                "marker": False,
                "content": replacement_text,
                "role": "system",
                "injection_position": 0,
                "injection_depth": 4,
                "forbid_overrides": False,
                "enabled": True,
            }
        )
        self._sync_prompt_order(payload, identifier, True)
        self.save_setvar_payload(payload)
        return value

    def clear_variable(self, name: str) -> None:
        """把变量值清空。"""
        self.set_variable(name, "")

    def _sync_prompt_order(
        self,
        payload: dict[str, Any],
        identifier: str,
        enabled: bool,
    ) -> None:
        """更新 prompt_order 中对应 identifier 的启用状态，没有则追加。"""
        if not identifier:
            return
        order_group = _first_order(payload)
        order_list = order_group.setdefault("order", [])
        if not isinstance(order_list, list):
            order_list = []
            order_group["order"] = order_list

        for item in order_list:
            if isinstance(item, dict) and item.get("identifier") == identifier:
                item["enabled"] = enabled
                return
        order_list.append({"identifier": identifier, "enabled": enabled})

    def find_prompt(self, key: str) -> tuple[dict[str, Any], int]:
        """按 identifier、名称或注入顺序的序号查找 prompt。"""
        payload = self.load_setvar_payload()
        return self._find_prompt_in(payload, key)

    @staticmethod
    def _find_prompt_in(
        payload: dict[str, Any],
        key: str,
    ) -> tuple[dict[str, Any], int]:
        """在给定 payload 中按注入顺序查找 prompt。"""
        prompts = _canonical_prompt_sequence(payload)
        if not prompts:
            raise ValueError("setvar.json 中没有提示词")
        for index, prompt in enumerate(prompts, start=1):
            if _prompt_matches(prompt, key, index):
                return prompt, index
        raise ValueError(f"未找到提示词: {key}")

    def set_prompt_enabled(self, key: str, enabled: bool) -> dict[str, Any]:
        """启用/禁用 setvar.json 中的一条提示词并保存。"""
        payload = self.load_setvar_payload()
        prompt, _ = self._find_prompt_in(payload, key)
        prompt["enabled"] = enabled
        self._sync_prompt_order(
            payload,
            str(prompt.get("identifier", "")),
            enabled,
        )
        self.save_setvar_payload(payload)
        return prompt

    # ----- WebUI 栏目读写 -----
    @staticmethod
    def _prompt_to_item(prompt: dict[str, Any]) -> dict[str, Any]:
        return {
            "identifier": str(prompt.get("identifier", "") or ""),
            "name": str(prompt.get("name", "") or ""),
            "enabled": bool(prompt.get("enabled", True)),
            "role": str(prompt.get("role", "system") or "system"),
            "content": str(prompt.get("content", "") or ""),
        }

    @staticmethod
    def _item_to_prompt(
        item: dict[str, Any],
        existing: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        base: dict[str, Any] = {
            "identifier": str(item.get("identifier", "") or "") or str(uuid.uuid4()),
            "name": str(item.get("name", "") or ""),
            "system_prompt": False,
            "marker": False,
            "content": str(item.get("content", "") or ""),
            "role": str(item.get("role", "system") or "system"),
            "injection_position": 0,
            "injection_depth": 4,
            "forbid_overrides": False,
            "enabled": bool(item.get("enabled", True)),
        }
        if existing:
            base = {**existing}
            base.update(
                {
                    "identifier": str(item.get("identifier", "") or "")
                    or str(existing.get("identifier", ""))
                    or str(uuid.uuid4()),
                    "name": str(item.get("name", "") or ""),
                    "content": str(item.get("content", "") or ""),
                    "role": str(item.get("role", "system") or "system"),
                    "enabled": bool(item.get("enabled", True)),
                }
            )
        return base

    @staticmethod
    def _regex_to_item(entry: dict[str, Any]) -> dict[str, Any]:
        substitute_raw = entry.get("substituteRegex", 1)
        substitute = int(substitute_raw) if substitute_raw is not None else 1
        return {
            "id": str(entry.get("id", "") or ""),
            "scriptName": str(entry.get("scriptName", "") or ""),
            "findRegex": str(entry.get("findRegex", "") or ""),
            "replaceString": str(entry.get("replaceString", "") or ""),
            "disabled": bool(entry.get("disabled", False)),
            "markdownOnly": bool(entry.get("markdownOnly", False)),
            "promptOnly": bool(entry.get("promptOnly", False)),
            "placement": list(entry.get("placement") or []),
            "substituteRegex": substitute,
            "trimStrings": list(entry.get("trimStrings") or []),
        }

    @staticmethod
    def _item_to_regex(
        item: dict[str, Any],
        existing: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        substitute_raw = item.get("substituteRegex", 1)
        substitute = int(substitute_raw) if substitute_raw is not None else 1
        base: dict[str, Any] = {
            "id": str(item.get("id", "") or "") or str(uuid.uuid4()),
            "scriptName": str(item.get("scriptName", "") or ""),
            "findRegex": str(item.get("findRegex", "") or ""),
            "replaceString": str(item.get("replaceString", "") or ""),
            "trimStrings": [],
            "placement": list(item.get("placement") or []),
            "disabled": bool(item.get("disabled", False)),
            "markdownOnly": bool(item.get("markdownOnly", False)),
            "promptOnly": bool(item.get("promptOnly", False)),
            "substituteRegex": substitute,
        }
        if existing:
            base = {**existing}
            base.update(
                {
                    "id": str(item.get("id", "") or "")
                    or str(existing.get("id", ""))
                    or str(uuid.uuid4()),
                    "scriptName": str(item.get("scriptName", "") or ""),
                    "findRegex": str(item.get("findRegex", "") or ""),
                    "replaceString": str(item.get("replaceString", "") or ""),
                    "placement": list(item.get("placement") or []),
                    "disabled": bool(item.get("disabled", False)),
                    "markdownOnly": bool(item.get("markdownOnly", False)),
                    "promptOnly": bool(item.get("promptOnly", False)),
                    "substituteRegex": substitute,
                    "trimStrings": list(item.get("trimStrings") or []),
                }
            )
        base.pop("runOnEdit", None)
        base.pop("minDepth", None)
        base.pop("maxDepth", None)
        return base

    def list_setvar_items(self) -> list[dict[str, Any]]:
        """按注入顺序列出 WebUI 使用的预设条目。"""
        payload = self.load_setvar_payload()
        return [
            self._prompt_to_item(prompt)
            for prompt in _canonical_prompt_sequence(payload)
        ]

    def list_ordered_prompt_items(self) -> list[dict[str, Any]]:
        """按统一顺序列出只读条目与酒馆预设条目，供 WebUI 使用。"""
        payload = self.load_setvar_payload()
        setvar_items = self.list_setvar_items()
        by_identifier = {
            str(item["identifier"]): item
            for item in setvar_items
            if item.get("identifier")
        }
        readonly_items = {
            str(item["identifier"]): item for item in self.readonly_prompt_items()
        }

        ordered: list[dict[str, Any]] = []
        seen: set[str] = set()
        for identifier in self.resolve_mofox_order(payload):
            if identifier in readonly_items:
                ordered.append(dict(readonly_items[identifier]))
                seen.add(identifier)
                continue
            item = by_identifier.get(identifier)
            if item is not None:
                ordered.append(dict(item))
                seen.add(identifier)

        for item in setvar_items:
            identifier = str(item.get("identifier", "") or "")
            if identifier and identifier not in seen:
                ordered.append(item)

        for identifier, item in readonly_items.items():
            if identifier not in seen:
                ordered.append(dict(item))

        return ordered

    def save_setvar_items(self, items: list[dict[str, Any]]) -> int:
        """保存 WebUI 编辑后的预设条目，并按提交顺序重建 prompt_order。"""
        payload = self.load_setvar_payload()
        readonly_identifiers = self._readonly_identifiers()
        editable_items = [
            item
            for item in items
            if isinstance(item, dict)
            and str(item.get("identifier", "") or "") not in readonly_identifiers
        ]
        existing_by_id = {
            str(prompt.get("identifier", "")): prompt
            for prompt in payload.get("prompts", [])
            if isinstance(prompt, dict) and prompt.get("identifier")
        }
        prompts = [
            self._item_to_prompt(
                item,
                existing_by_id.get(str(item.get("identifier", "") or "")),
            )
            for item in editable_items
            if isinstance(item, dict)
        ]
        payload["prompts"] = prompts
        self._rebuild_prompt_order(payload, prompts)
        submitted_order = [
            str(item.get("identifier", "") or "")
            for item in items
            if isinstance(item, dict) and str(item.get("identifier", "") or "")
        ]
        self._save_mofox_order_from_submitted(payload, submitted_order)
        self.save_setvar_payload(payload)
        return len(prompts)

    def _save_mofox_order_from_submitted(
        self,
        payload: dict[str, Any],
        submitted_order: list[str],
    ) -> None:
        """根据 WebUI 提交顺序写入统一顺序，并补齐缺失条目。"""
        normalized: list[str] = []
        seen: set[str] = set()
        for identifier in submitted_order:
            if identifier == "mofox_function":
                identifier = "mofox_tool"
            if identifier and identifier not in seen:
                normalized.append(identifier)
                seen.add(identifier)

        fixed_ids = list(_MOFOX_FIXED_ORDER_IDS)
        prompt_ids = self._prompt_identifiers(payload)
        for identifier in [*fixed_ids, *prompt_ids]:
            if identifier not in seen:
                if identifier == "mofox_system" and normalized:
                    normalized.insert(0, identifier)
                else:
                    normalized.append(identifier)
                seen.add(identifier)
        payload["mofox_order"] = normalized

    @staticmethod
    def _rebuild_prompt_order(
        payload: dict[str, Any],
        prompts: list[dict[str, Any]],
    ) -> None:
        """把第一组 prompt_order 重写为给定提示词的顺序与启用状态。"""
        order_group = _first_order(payload)
        order_group["order"] = [
            {
                "identifier": str(prompt.get("identifier", "") or ""),
                "enabled": bool(prompt.get("enabled", True)),
            }
            for prompt in prompts
            if str(prompt.get("identifier", "") or "")
        ]

    def list_regex_items(self) -> list[dict[str, Any]]:
        """列出 WebUI 使用的正则条目。"""
        return [self._regex_to_item(entry) for entry in self.load_regex_entries()]

    def save_regex_items(self, items: list[dict[str, Any]]) -> int:
        """保存 WebUI 编辑后的正则条目。"""
        existing_by_id = {
            str(entry.get("id", "")): entry
            for entry in self.load_regex_entries()
            if entry.get("id")
        }
        entries = [
            self._item_to_regex(
                item,
                existing_by_id.get(str(item.get("id", "") or "")),
            )
            for item in items
            if isinstance(item, dict)
        ]
        self.save_regex_entries(entries)
        return len(entries)

    def import_regex_entries(self, entries: list[dict[str, Any]]) -> dict[str, Any]:
        """按 id 合并导入正则条目。"""
        current = self.load_regex_entries()
        by_id = {
            str(entry.get("id", "")): entry for entry in current if entry.get("id")
        }
        imported = 0

        for raw_entry in entries:
            if not isinstance(raw_entry, dict):
                continue
            filtered_entry = {
                key: value
                for key, value in raw_entry.items()
                if key not in {"runOnEdit", "minDepth", "maxDepth"}
            }
            entry_id = str(raw_entry.get("id", "") or "")
            existing = by_id.get(entry_id)
            if existing and entry_id:
                existing.update(filtered_entry)
                existing.pop("runOnEdit", None)
                existing.pop("minDepth", None)
                existing.pop("maxDepth", None)
            else:
                current.append(filtered_entry)
                by_id[entry_id] = current[-1]
            imported += 1

        self.save_regex_entries(current)
        return {"count": imported, "total": len(current)}

    def import_setvar_preset(self, payload: dict[str, Any]) -> dict[str, Any]:
        """导入 SillyTavern 预设并转换适配 MoFox 请求。

        - 跳过酒馆标记位提示词（聊天记录、角色卡、世界书等由 MoFox 自身构建）；
        - 采用导入预设的 prompt_order 顺序与启用状态；
        - 首次导入时停用与 MoFox 主回复指令重复的内置提示词（main / nsfw）。
        """
        raw_prompts = payload.get("prompts")
        if not isinstance(raw_prompts, list):
            raw_prompts = []
        imported_order = dict(_first_order_entries(payload))

        current_payload = self.load_setvar_payload()
        current_prompts = [
            item
            for item in current_payload.get("prompts", [])
            if isinstance(item, dict)
        ]
        current_sequence = _canonical_prompt_sequence(current_payload)
        by_identifier = {
            str(prompt.get("identifier", "")): prompt
            for prompt in current_prompts
            if prompt.get("identifier")
        }

        skipped_markers = 0
        disabled_defaults = 0
        imported: list[dict[str, Any]] = []
        marker_ids: set[str] = set()

        for raw_prompt in raw_prompts:
            if not isinstance(raw_prompt, dict):
                continue
            identifier = str(raw_prompt.get("identifier", "") or "")
            if (
                bool(raw_prompt.get("marker", False))
                or identifier in _MARKER_IDENTIFIERS
            ):
                skipped_markers += 1
                if identifier:
                    marker_ids.add(identifier)
                continue

            item = self._prompt_to_item(raw_prompt)
            existing = by_identifier.get(identifier) if identifier else None
            normalized = self._item_to_prompt(item, existing)
            if identifier in imported_order:
                normalized["enabled"] = imported_order[identifier]
            if identifier in _MOFOX_REDUNDANT_IDENTIFIERS and existing is None:
                normalized["enabled"] = False
                disabled_defaults += 1
            imported.append(normalized)
            if identifier:
                by_identifier[identifier] = normalized

        imported_sequence = _sort_prompts_by_order(imported, imported_order)
        imported_ids = {
            str(prompt.get("identifier", "") or "") for prompt in imported_sequence
        }
        kept: list[dict[str, Any]] = []
        for prompt in current_sequence:
            identifier = str(prompt.get("identifier", "") or "")
            # 标记位在 MoFox 中永远无意义，导入时一并清理旧残留
            if bool(prompt.get("marker", False)) or identifier in _MARKER_IDENTIFIERS:
                continue
            if identifier and identifier in imported_ids:
                continue
            kept.append(prompt)

        final_sequence = imported_sequence + kept
        current_payload["prompts"] = final_sequence
        self._rebuild_prompt_order(current_payload, final_sequence)
        self.save_setvar_payload(current_payload)
        return {
            "count": len(imported),
            "total": len(final_sequence),
            "skipped_markers": skipped_markers,
            "disabled_defaults": disabled_defaults,
        }

    def detect_and_import_payload(self, payload: Any) -> dict[str, Any]:
        """自动识别 JSON 类型并导入对应内容。"""
        regex_entries: list[dict[str, Any]] | None = None
        preset_payload: dict[str, Any] | None = None

        if isinstance(payload, list):
            entries = [item for item in payload if isinstance(item, dict)]
            if not entries:
                raise ValueError("JSON 数组为空")
            if any(
                key in entry
                for entry in entries
                for key in (
                    "findRegex",
                    "find_regex",
                    "replaceString",
                    "replace_string",
                    "scriptName",
                )
            ):
                regex_entries = entries
            elif any(
                key in entry for entry in entries for key in ("content", "prompts")
            ):
                preset_payload = {"prompts": entries}
            else:
                raise ValueError("无法识别的 JSON 数组")
        elif isinstance(payload, dict):
            regex_scripts = payload.get("regex_scripts")
            if not isinstance(regex_scripts, list):
                extensions = payload.get("extensions")
                if isinstance(extensions, dict):
                    regex_scripts = extensions.get("regex_scripts")
            if not isinstance(regex_scripts, list):
                regex_scripts = payload.get("scripts")

            if isinstance(regex_scripts, list):
                found_scripts = [
                    item for item in regex_scripts if isinstance(item, dict)
                ]
                if found_scripts:
                    regex_entries = found_scripts

            if regex_entries is None and any(
                key in payload
                for key in (
                    "findRegex",
                    "find_regex",
                    "replaceString",
                    "replace_string",
                    "scriptName",
                    "script_name",
                )
            ):
                regex_entries = [payload]

            raw_prompts = payload.get("prompts")
            prompt_order = payload.get("prompt_order")
            source_data = payload
            if not isinstance(raw_prompts, list):
                data = payload.get("data")
                if isinstance(data, dict):
                    raw_prompts = data.get("prompts")
                    prompt_order = data.get("prompt_order") or prompt_order
                    source_data = data

            if isinstance(raw_prompts, list):
                preset_payload = {
                    "prompts": raw_prompts,
                    "prompt_order": prompt_order
                    if isinstance(prompt_order, list)
                    else [],
                }
                if isinstance(source_data, dict) and source_data is not payload:
                    for key in (
                        "impersonation_prompt",
                        "new_chat_prompt",
                        "continue_nudge_prompt",
                        "scenario_format",
                        "personality_format",
                    ):
                        if key in payload:
                            preset_payload[key] = payload[key]
        else:
            raise ValueError("JSON 顶层必须是对象或数组")

        if regex_entries is None and preset_payload is None:
            raise ValueError("无法识别为 SillyTavern 预设或正则文件")

        results: dict[str, Any] = {
            "regex_count": 0,
            "setvar_count": 0,
        }
        if regex_entries is not None:
            regex_result = self.import_regex_entries(regex_entries)
            results["regex_count"] = regex_result["count"]
            results["regex_total"] = regex_result["total"]
        if preset_payload is not None:
            setvar_result = self.import_setvar_preset(preset_payload)
            results["setvar_count"] = setvar_result["count"]
            results["setvar_total"] = setvar_result["total"]
            results["setvar_skipped_markers"] = setvar_result.get("skipped_markers", 0)
            results["setvar_disabled_defaults"] = setvar_result.get(
                "disabled_defaults", 0
            )

        if regex_entries is not None and preset_payload is not None:
            results["type"] = "mixed"
        elif regex_entries is not None:
            results["type"] = "regex"
        else:
            results["type"] = "setvar"
        results["count"] = results["regex_count"] + results["setvar_count"]
        return results

    def list_prompts(self) -> list[dict[str, Any]]:
        """按注入顺序列出 setvar.json 中的提示词。"""
        payload = self.load_setvar_payload()
        return _canonical_prompt_sequence(payload)
