"""正则规则执行引擎。"""

from __future__ import annotations

import re

from src.app.plugin_system.api.log_api import get_logger

from .config import RuleSection

logger = get_logger("tavern_preset_regex.engine")

_FLAG_MAP = {
    "i": re.IGNORECASE,
    "m": re.MULTILINE,
    "s": re.DOTALL,
    "x": re.VERBOSE,
}


def _parse_flags(flags: str) -> int:
    """把 "im" 之类的标志字符串转换为 re 标志位。"""
    result = 0
    for char in flags or "":
        flag = _FLAG_MAP.get(char.lower())
        if flag is not None:
            result |= flag
    return result


def _wrap_tags(replacement: str) -> tuple[str, str] | None:
    """从替换模板里取出「整体包裹」用的开闭标签。

    形如 ``<tag>\\n$1\\n</tag>``（转换后是 ``<tag>\\n\\1\\n</tag>``）的替换会把
    整段内容包起来。若同一个请求被反复套用（工具调用后的二次请求会再次走到
    这里），标签就会一层层堆积。这里解析出这对标签，供
    :func:`_already_wrapped` 做幂等判断。
    """
    placeholder = ""
    for candidate in ("$1", "\\1", "\\g<1>", "${1}"):
        if candidate in replacement:
            placeholder = candidate
            break
    if not placeholder:
        return None

    before, _, after = replacement.partition(placeholder)
    opening = before.strip()
    closing = after.strip()
    if not opening or not closing:
        return None
    if not (opening.startswith("<") and closing.startswith("</")):
        return None
    if not opening.endswith(">") or not closing.endswith(">"):
        return None
    return opening, closing


def _already_wrapped(text: str, opening: str, closing: str) -> bool:
    """判断文本是否已经被这对标签包裹过（只看最外层）。"""
    stripped = text.strip()
    return stripped.startswith(opening) and stripped.endswith(closing)


def apply_rules(text: str, rules: list[RuleSection], target: str) -> str:
    """按声明顺序对文本执行匹配目标的正则规则。"""
    if not text:
        return text

    current = text
    for rule in rules:
        if not rule.enabled or not rule.pattern:
            continue
        if rule.target not in ("both", target):
            continue

        try:
            pattern = re.compile(rule.pattern, _parse_flags(rule.flags))
            # 幂等保护：整段包裹型规则对已经包裹过的内容不再重复套用，
            # 否则每经过一次请求就会多包一层（<tag><tag>…）。
            tags = _wrap_tags(rule.replacement)
            if tags is not None and _already_wrapped(current, *tags):
                continue
            current = pattern.sub(rule.replacement, current)
        except re.error as exc:
            logger.warning(
                f"规则 {rule.name or rule.pattern!r} 编译失败，已跳过: {exc}"
            )
            continue

    return current
