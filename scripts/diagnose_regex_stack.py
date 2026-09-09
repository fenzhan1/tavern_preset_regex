"""复现正则规则的 <interactive_input> 堆积。"""

from __future__ import annotations

import importlib
import json
import sys
from pathlib import Path

PLUGIN_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PLUGIN_ROOT.parent))
sys.path.insert(0, str(Path("D:/Neo-MoFox_Bots/myplugins/neo-mofox")))

engine = importlib.import_module("tavern_preset_regex.engine")
store = importlib.import_module("tavern_preset_regex.tavern_store")

# 用线上 regex.json 的副本（--config 指定）；没有就用本地 data
CONFIG = Path(sys.argv[1]) if len(sys.argv) > 1 else PLUGIN_ROOT / "data" / "regex.json"
data = json.loads(CONFIG.read_text(encoding="utf-8"))
entries = data if isinstance(data, list) else data.get("regex_entries") or []

rules = []
for entry in entries:
    rule = store._entry_to_rule(entry)
    if rule is not None:
        rules.append(rule)

print(f"载入 {len(rules)} 条规则：")
for index, rule in enumerate(rules):
    print(
        f"  #{index} enabled={rule.enabled} target={rule.target!r} "
        f"pattern={rule.pattern[:70]!r} flags={rule.flags!r}"
    )
    if "aether opus" in rule.name:
        print(f"      replacement={rule.replacement!r}")
        print(f"      _wrap_tags -> {engine._wrap_tags(rule.replacement)}")

text = "预填充内容：明白了。"
print("\n初始：", repr(text))
for round_index in range(1, 4):
    text = engine.apply_rules(text, rules, "input")
    nested = text.count("<interactive_input>")
    print(f"\n第 {round_index} 轮后（<interactive_input> 出现 {nested} 次）：")
    print(" ", repr(text[:300]))

# ---- 方案验证：让 strip 规则在 input 阶段生效 ----
print("\n" + "=" * 60)
print("方案：把 #1/#2 的 target 改成 input，并排到 #6 之前")
fixed_entries = []
for entry in entries:
    copy = dict(entry)
    name = str(copy.get("scriptName", ""))
    if name in ("/<interactive_input>/g", "/</interactive_input>/g"):
        copy["markdownOnly"] = False
        copy["promptOnly"] = True
        copy["placement"] = [1]
        # 连同换行一起吃掉，避免残留空行
        if name == "/<interactive_input>/g":
            copy["findRegex"] = r"/<interactive_input>\n?/g"
        else:
            copy["findRegex"] = r"/\n?</interactive_input>/g"
    fixed_entries.append(copy)

fixed_rules = [r for r in (store._entry_to_rule(e) for e in fixed_entries) if r]
# 把两条 strip 规则挪到 wrapper 之前
strip_names = {"/<interactive_input>/g", "/</interactive_input>/g"}
strip = [r for r in fixed_rules if r.name in strip_names]
others = [r for r in fixed_rules if r.name not in strip_names]
# 保持 #6 在 strip 之后：把 strip 插到名字含 aether opus 的规则前面
ordered = []
for rule in others:
    if "aether opus" in rule.name:
        ordered.extend(strip)
    ordered.append(rule)
if len(ordered) == len(others):
    ordered = strip + others
fixed_rules = ordered

for index, rule in enumerate(fixed_rules):
    if rule.name in strip_names or "aether opus" in rule.name:
        print(f"  #{index} {rule.name!r} target={rule.target} pattern={rule.pattern!r}")

text2 = "预填充内容：明白了。"
for round_index in range(1, 4):
    text2 = engine.apply_rules(text2, fixed_rules, "input")
    nested = text2.count("<interactive_input>")
    print(f"\n第 {round_index} 轮后（<interactive_input> 出现 {nested} 次）：")
    print(" ", repr(text2[:200]))

