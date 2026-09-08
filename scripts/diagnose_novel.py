"""端到端验证小说分段注入：请求前取段落 → 注入 payload → 推进进度。

用法（在 neo-mofox 根目录执行，或任意目录，脚本会自行设置路径）：

    python scripts/diagnose_novel.py
"""

# ruff: noqa: I001 - 需要先补齐 sys.path 才能导入插件模块

from __future__ import annotations

import asyncio
import importlib
import io
import json
import shutil
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace

if hasattr(sys.stdout, "buffer"):
    sys.stdout = io.TextIOWrapper(
        sys.stdout.buffer, encoding="utf-8", errors="replace", line_buffering=True
    )

PLUGIN_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PLUGIN_ROOT.parent))

NEO_MOFOX = Path("D:/Neo-MoFox_Bots/myplugins/neo-mofox")
if NEO_MOFOX.is_dir():
    sys.path.insert(0, str(NEO_MOFOX))

from src.kernel.llm import LLMPayload, ROLE, Text
from src.kernel.llm.context_structure import validate_payload_sequence

event_handler = importlib.import_module("tavern_preset_regex.event_handler")
config_module = importlib.import_module("tavern_preset_regex.config")
TavernRequestHandler = event_handler.TavernRequestHandler
TavernRegexConfig = config_module.TavernRegexConfig
NovelSection = config_module.NovelSection

NOVEL_TEXT = """第一章 初到小镇

少年背着行囊走下车，站台的木牌被风吹得吱呀作响。

第二章 旧书店

书店的门铃响了一声，柜台后面没有人。

第三章 雨夜

雨点砸在铁皮屋顶上，整条街只剩下这一盏灯还亮着。
"""

SETVAR = {
    "prompts": [
        {
            "identifier": "p1",
            "name": "系统规则",
            "role": "system",
            "content": "规则内容",
            "enabled": True,
        },
        {
            "identifier": "reader",
            "name": "读小说变量",
            "role": "user",
            "content": "<novel>{{getvar::current_chapter}}</novel>",
            "enabled": True,
        },
    ],
    "prompt_order": [
        {"identifier": "p1", "enabled": True},
        {"identifier": "reader", "enabled": True},
    ],
    "mofox_order": [
        "mofox_system",
        "p1",
        "novel_current",
        "reader",
        "mofox_tool",
        "mofox_user",
    ],
}


def build_plugin(
    data_dir: Path, *, enabled: bool = True, batch: int = 1
) -> SimpleNamespace:
    """构造带真实配置模型的假插件对象（TavernDataService 依赖 isinstance 判断）。"""
    config = TavernRegexConfig()
    config.plugin.data_dir = str(data_dir)
    config.plugin.enabled = True
    config.plugin.inject_setvar = True
    config.plugin.use_tavern_preset_regex = True
    config.plugin.main_request_names = ["neo_default_chatter"]
    config.plugin.debug_log = True

    config.novel = NovelSection(
        enabled=enabled,
        file="",
        split_mode="auto",
        batch_size=batch,
        variable_name="current_chapter",
        role="system",
        entry_enabled=True,
        inject_when_empty=False,
    )
    return SimpleNamespace(plugin_name="tavern_preset_regex", config=config)


def build_payloads() -> list[LLMPayload]:
    return [
        LLMPayload(ROLE.SYSTEM, [Text("系统提示词")]),
        LLMPayload(ROLE.TOOL, [Text("工具占位")]),
        LLMPayload(ROLE.USER, [Text("用户输入")]),
    ]


async def run_round(plugin: SimpleNamespace, stream_id: str) -> list[LLMPayload]:
    handler = TavernRequestHandler(plugin)
    payloads = build_payloads()
    params = {
        "request_name": "neo_default_chatter",
        "payloads": payloads,
        "meta_data": {"stream_id": stream_id},
    }
    await handler.execute("before_llm_request", params)
    return params["payloads"]


def show(payloads: list[LLMPayload]) -> None:
    for index, payload in enumerate(payloads):
        text = "".join(part.text for part in payload.content if isinstance(part, Text))
        preview = text.replace("\n", "\\n")[:70]
        print(f"  [{index:>2}] {payload.role!s:<14} {preview}")


async def main() -> int:
    workdir = Path(tempfile.mkdtemp(prefix="tavern_novel_"))
    try:
        (workdir / "novel").mkdir(parents=True)
        (workdir / "novel" / "demo.txt").write_text(NOVEL_TEXT, encoding="utf-8")
        (workdir / "setvar.json").write_text(
            json.dumps(SETVAR, ensure_ascii=False, indent=2), encoding="utf-8"
        )

        plugin = build_plugin(workdir)
        print("=== 第 1 轮请求（stream=A） ===")
        payloads = await run_round(plugin, "stream-A")
        show(payloads)
        validate_payload_sequence(payloads, allow_incomplete_tail=False)

        print("\n=== 第 2 轮请求（stream=A，应推进到第二章） ===")
        payloads = await run_round(plugin, "stream-A")
        show(payloads)
        validate_payload_sequence(payloads, allow_incomplete_tail=False)

        print("\n=== 第 1 轮请求（stream=B，进度应独立，仍为第一章） ===")
        payloads = await run_round(plugin, "stream-B")
        show(payloads)

        progress = json.loads((workdir / "novel" / "progress.json").read_text("utf-8"))
        print("\n进度文件：", json.dumps(progress, ensure_ascii=False))

        print("\n=== 关闭小说功能（应无小说条目） ===")
        plugin_off = build_plugin(workdir, enabled=False)
        payloads = await run_round(plugin_off, "stream-A")
        show(payloads)

        print("\n=== 批量注入 2 段 ===")
        plugin_batch = build_plugin(workdir, batch=2)
        payloads = await run_round(plugin_batch, "stream-C")
        show(payloads)
        return 0
    finally:
        shutil.rmtree(workdir, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
