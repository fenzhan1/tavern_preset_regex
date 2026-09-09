"""正则引擎的回归测试（重点是「整段包裹」规则的幂等性）。

运行方式（使用 neo-mofox 的虚拟环境）：

    D:\\Neo-MoFox_Bots\\myplugins\\neo-mofox\\.venv\\Scripts\\python.exe -m pytest tests
"""

# ruff: noqa: I001 - 需要先补齐 sys.path 才能导入 neo-mofox 与插件模块

from __future__ import annotations

import importlib
import sys
from pathlib import Path

PLUGIN_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PLUGIN_ROOT.parent))

NEO_MOFOX = Path("D:/Neo-MoFox_Bots/myplugins/neo-mofox")
if NEO_MOFOX.is_dir():
    sys.path.insert(0, str(NEO_MOFOX))

engine = importlib.import_module("tavern_preset_regex.engine")
config_module = importlib.import_module("tavern_preset_regex.config")

RuleSection = config_module.RuleSection
apply_rules = engine.apply_rules


def make_wrap_rule(
    *,
    name: str = "wrapper",
    pattern: str = r"^([\s\S]*)$",
    replacement: str = "<interactive_input>\n\\1\n</interactive_input>",
    target: str = "input",
    enabled: bool = True,
) -> RuleSection:
    return RuleSection(
        name=name,
        enabled=enabled,
        target=target,
        pattern=pattern,
        replacement=replacement,
    )


def test_wrap_rule_is_idempotent() -> None:
    """整段包裹规则对已包裹内容不再重复套用（否则每轮请求多一层标签）。"""
    rules = [make_wrap_rule()]
    text = "预填充内容：明白了。"

    first = apply_rules(text, rules, "input")
    assert first.count("<interactive_input>") == 1

    # 反复经过同一个请求管道，标签数必须保持 1
    for _ in range(4):
        text = apply_rules(first, rules, "input")
        assert text.count("<interactive_input>") == 1, text
        assert text.count("</interactive_input>") == 1, text
        assert text == first


def test_wrap_rule_detects_tavern_dollar_syntax() -> None:
    """SillyTavern 的 $1 与 Python 的 \\1 都要能识别成包裹模板。"""
    assert engine._wrap_tags("<t>\n$1\n</t>") == ("<t>", "</t>")
    assert engine._wrap_tags("<t>\n\\1\n</t>") == ("<t>", "</t>")
    assert engine._wrap_tags("<t>\\g<1></t>") == ("<t>", "</t>")
    # 非包裹型替换不应被误判
    assert engine._wrap_tags("前缀 $1 后缀") is None
    assert engine._wrap_tags("") is None


def test_strip_rule_runs_on_input_before_wrapper() -> None:
    """strip → wrap 组合在 input 阶段也能稳定收敛到单层。"""
    strip_open = RuleSection(
        name="strip-open",
        enabled=True,
        target="input",
        pattern=r"<interactive_input>\n?",
        replacement="",
    )
    strip_close = RuleSection(
        name="strip-close",
        enabled=True,
        target="input",
        pattern=r"\n?</interactive_input>",
        replacement="",
    )
    wrapper = make_wrap_rule()
    rules = [strip_open, strip_close, wrapper]

    text = "<interactive_input>\n<interactive_input>\n旧内容\n</interactive_input>\n</interactive_input>"
    for _ in range(4):
        text = apply_rules(text, rules, "input")
        assert text.count("<interactive_input>") == 1, text
    assert "旧内容" in text


def test_plain_rule_still_applies_every_time() -> None:
    """非包裹型规则不受幂等保护影响，仍按正常语义执行。"""
    rule = RuleSection(
        name="replace",
        enabled=True,
        target="input",
        pattern=r"猫",
        replacement="狐",
    )
    assert apply_rules("猫猫", [rule], "input") == "狐狐"
    assert apply_rules("狐狐", [rule], "input") == "狐狐"


def test_output_target_is_not_affected_by_input_pass() -> None:
    """target=output 的规则不会在 input 阶段生效。"""
    rule = make_wrap_rule(target="output")
    text = "内容"
    assert apply_rules(text, [rule], "input") == text
    assert apply_rules(text, [rule], "output").count("<interactive_input>") == 1
