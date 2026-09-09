"""调试 _wrap_tags 的解析。"""

from __future__ import annotations

import importlib
import sys
from pathlib import Path

PLUGIN_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PLUGIN_ROOT.parent))
sys.path.insert(0, str(Path("D:/Neo-MoFox_Bots/myplugins/neo-mofox")))

engine = importlib.import_module("tavern_preset_regex.engine")

samples = [
    "<interactive_input>\n$1\n</interactive_input>",
    "<interactive_input>\n$1\n</interactive_input>",
    "<tag>$1</tag>",
    "prefix $1 suffix",
]
for sample in samples:
    print(repr(sample))
    print("  has $1:", "$1" in sample)
    print("  _wrap_tags:", engine._wrap_tags(sample))
