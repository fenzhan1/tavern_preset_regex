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
            current = pattern.sub(rule.replacement, current)
        except re.error as exc:
            logger.warning(
                f"规则 {rule.name or rule.pattern!r} 编译失败，已跳过: {exc}"
            )
            continue

    return current

