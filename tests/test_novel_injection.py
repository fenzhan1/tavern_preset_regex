"""小说分段注入的回归测试。

运行方式（使用 neo-mofox 的虚拟环境）：

    D:\\Neo-MoFox_Bots\\myplugins\\neo-mofox\\.venv\\Scripts\\python.exe -m pytest tests
"""

# ruff: noqa: I001 - 需要先补齐 sys.path 才能导入 neo-mofox 与插件模块

from __future__ import annotations

import asyncio
import importlib
import json
import sys
from pathlib import Path

import pytest

PLUGIN_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PLUGIN_ROOT.parent))

NEO_MOFOX = Path("D:/Neo-MoFox_Bots/myplugins/neo-mofox")
if NEO_MOFOX.is_dir():
    sys.path.insert(0, str(NEO_MOFOX))

from src.kernel.llm import LLMPayload, ROLE, Text, ToolCall, ToolResult

event_handler = importlib.import_module("tavern_preset_regex.event_handler")
config_module = importlib.import_module("tavern_preset_regex.config")
novel_module = importlib.import_module("tavern_preset_regex.novel_store")
tavern_store = importlib.import_module("tavern_preset_regex.tavern_store")

NovelService = novel_module.NovelService
split_novel = novel_module.split_novel
detect_chapter_format = novel_module.detect_chapter_format
TavernDataService = tavern_store.TavernDataService
TavernRegexConfig = config_module.TavernRegexConfig
NovelSection = config_module.NovelSection
TavernRequestHandler = event_handler.TavernRequestHandler

NOVEL_TEXT = """第一章 初到小镇

少年背着行囊走下车，站台的木牌被风吹得吱呀作响。

第二章 旧书店

书店的门铃响了一声，柜台后面没有人。

第三章 雨夜

雨点砸在铁皮屋顶上，整条街只剩下这一盏灯还亮着。
"""

PLAIN_TEXT = """第一段没有标题，只是一些普通文字。
第二段继续写下去，用来测试按字数切分。
第三段也还是普通文字。
"""


def write_novel(root: Path, text: str = NOVEL_TEXT, name: str = "demo.txt") -> Path:
    novel_dir = root / "novel"
    novel_dir.mkdir(parents=True, exist_ok=True)
    path = novel_dir / name
    path.write_text(text, encoding="utf-8")
    return path


def make_service(root: Path, **novel_overrides) -> TavernDataService:
    """构造使用临时目录的 TavernDataService。"""
    config = TavernRegexConfig()
    config.plugin.data_dir = str(root)
    novel = NovelSection(**novel_overrides)
    config.novel = novel
    plugin = type("_FakePlugin", (), {"config": config})()
    return TavernDataService(tavern_dir=root, plugin=plugin)


# ----- 分段 -----


def test_detect_chapter_format_finds_chinese_headings() -> None:
    detected = detect_chapter_format(NOVEL_TEXT)
    assert detected["mode"] == "chapter"
    assert detected["count"] == 3


def test_detect_chapter_format_falls_back_to_char() -> None:
    assert detect_chapter_format(PLAIN_TEXT)["mode"] == "char"


def test_split_by_chapter_keeps_titles() -> None:
    result = split_novel(NOVEL_TEXT, mode="auto")
    assert result["mode"] == "chapter"
    assert len(result["segments"]) == 3
    assert result["segments"][0].startswith("第一章")
    assert "木牌" in result["segments"][0]


def test_split_by_char_respects_size() -> None:
    result = split_novel(NOVEL_TEXT, mode="char", char_size=20)
    assert result["mode"] == "char"
    assert all(len(item) <= 25 for item in result["segments"])
    assert len(result["segments"]) > 1


def test_split_by_line() -> None:
    result = split_novel(NOVEL_TEXT, mode="line", lines_per_segment=2)
    assert result["mode"] == "line"
    # 原文每章之间有空行，2 行一段时标题与正文各成一段
    assert len(result["segments"]) == 6
    assert result["segments"][0] == "第一章 初到小镇"


def test_split_auto_without_headings_uses_char() -> None:
    result = split_novel(PLAIN_TEXT, mode="auto", char_size=30)
    assert result["mode"] == "char"
    assert result["segments"]


def test_custom_chapter_pattern() -> None:
    text = "# 一\n内容一\n# 二\n内容二\n"
    result = split_novel(text, mode="chapter", chapter_pattern=r"^#")
    assert result["mode"] == "chapter"
    assert len(result["segments"]) == 2


# ----- 进度 -----


def test_progress_is_isolated_per_stream(tmp_path: Path) -> None:
    write_novel(tmp_path)
    service = NovelService(tmp_path / "novel")
    segments = ["a", "b", "c"]

    first = service.advance("demo.txt", segments, stream_id="s1")
    assert first["content"] == "a"
    assert service.get_index("demo.txt", "s1") == 1
    assert service.get_index("demo.txt", "s2") == 0

    second = service.advance("demo.txt", segments, stream_id="s2")
    assert second["content"] == "a"
    assert service.get_index("demo.txt", "s2") == 1


def test_progress_batch_and_finished(tmp_path: Path) -> None:
    service = NovelService(tmp_path / "novel")
    segments = ["a", "b", "c"]

    result = service.advance("demo.txt", segments, batch_size=2, stream_id="s")
    assert result["content"] == "a\n\nb"
    assert (result["start"], result["end"]) == (1, 2)
    assert result["finished"] is False

    result = service.advance("demo.txt", segments, batch_size=2, stream_id="s")
    assert result["content"] == "c"
    assert result["finished"] is True

    stopped = service.advance("demo.txt", segments, stream_id="s")
    assert stopped["content"] == ""
    assert stopped["finished"] is True


def test_progress_loop_wraps_around(tmp_path: Path) -> None:
    service = NovelService(tmp_path / "novel")
    segments = ["a", "b"]
    service.set_index("demo.txt", 2, "s")

    result = service.advance("demo.txt", segments, loop=True, stream_id="s")
    assert result["content"] == "a"
    assert result["looped"] is True


def test_progress_persists_to_disk(tmp_path: Path) -> None:
    service = NovelService(tmp_path / "novel")
    service.set_index("demo.txt", 3, "s")
    payload = json.loads((tmp_path / "novel" / "progress.json").read_text("utf-8"))
    assert payload["novels"]["demo.txt"]["streams"]["s"] == 3


def test_resolve_file_rejects_path_traversal(tmp_path: Path) -> None:
    write_novel(tmp_path)
    service = NovelService(tmp_path / "novel")
    with pytest.raises(ValueError):
        service.resolve_file("../setvar.json")


def test_list_files_skips_readme(tmp_path: Path) -> None:
    """novel/ 里的 README.md 是说明文件，不能当成小说。"""
    write_novel(tmp_path)
    (tmp_path / "novel" / "README.md").write_text("目录说明", encoding="utf-8")
    service = NovelService(tmp_path / "novel")
    names = [item["name"] for item in service.list_files()]
    assert names == ["demo.txt"]


def test_active_file_ignores_readme_only_dir(tmp_path: Path) -> None:
    """目录里只有 README 时应视为没有小说，而不是把 README 当正文。"""
    novel_dir = tmp_path / "novel"
    novel_dir.mkdir(parents=True)
    (novel_dir / "README.md").write_text("说明", encoding="utf-8")
    service = make_service(tmp_path, enabled=True)
    assert service.active_novel_file() == ""
    assert service.novel_state("s")["total"] == 0


# ----- 设置 -----


def test_runtime_settings_override_toml(tmp_path: Path) -> None:
    write_novel(tmp_path)
    service = make_service(tmp_path, enabled=False, batch_size=1)
    assert service.novel_settings()["enabled"] is False

    service.save_novel_settings({"enabled": True, "batch_size": 3})
    settings = service.novel_settings()
    assert settings["enabled"] is True
    assert settings["batch_size"] == 3


def test_novel_role_comes_from_settings(tmp_path: Path) -> None:
    """小说条目的角色必须跟随配置，不能在注入时被固定成 system。"""
    write_novel(tmp_path)
    for role in ("system", "user", "assistant"):
        service = make_service(tmp_path, enabled=True, role=role)
        item = service.novel_prompt_item()
        assert item["role"] == role
        assert service.novel_settings()["role"] == role


def test_role_override_wins_and_only_stores_overrides(tmp_path: Path) -> None:
    """WebUI 改过的键写进 config.json 并覆盖 TOML；没改过的键仍用 TOML。"""
    write_novel(tmp_path)
    service = make_service(tmp_path, enabled=True, role="system", batch_size=5)

    service.save_novel_settings({"role": "user"})
    stored = json.loads((tmp_path / "novel" / "config.json").read_text("utf-8"))
    assert stored == {"role": "user"}, "config.json 不应写入默认值"

    # 角色被覆盖，但 batch_size 仍取 TOML
    settings = service.novel_settings()
    assert settings["role"] == "user"
    assert settings["batch_size"] == 5

    # 之后手改 TOML 的 role，仍会被 config.json 覆盖（UI 改动优先）
    service_toml_changed = make_service(tmp_path, enabled=True, role="assistant")
    assert service_toml_changed.novel_settings()["role"] == "user"
    assert service_toml_changed.novel_prompt_item()["role"] == "user"


def test_novel_state_reports_segments(tmp_path: Path) -> None:
    write_novel(tmp_path)
    service = make_service(tmp_path, enabled=True)
    state = service.novel_state("stream-1")
    assert state["active_file"] == "demo.txt"
    assert state["total"] == 3
    assert state["mode"] == "chapter"
    assert state["segment_titles"][0].startswith("第一章")
    assert state["index"] == 0


def test_jump_and_reset(tmp_path: Path) -> None:
    write_novel(tmp_path)
    service = make_service(tmp_path, enabled=True)

    result = service.jump_novel(2, "s")
    assert result["content"].startswith("第二章")
    assert service.novel_state("s")["index"] == 2

    service.reset_novel("s")
    assert service.novel_state("s")["index"] == 0


# ----- 请求注入 -----


def make_plugin(root: Path, **novel_overrides):
    config = TavernRegexConfig()
    config.plugin.data_dir = str(root)
    config.plugin.main_request_names = ["neo_default_chatter"]
    config.plugin.debug_log = False
    config.novel = NovelSection(**novel_overrides)
    return type("_FakePlugin", (), {"config": config})()


def setvar_payload(order: list[str]) -> dict:
    return {
        "prompts": [
            {
                "identifier": "reader",
                "name": "读小说变量",
                "role": "user",
                "content": "<novel>{{getvar::current_chapter}}</novel>",
                "enabled": True,
            }
        ],
        "prompt_order": [{"identifier": "reader", "enabled": True}],
        "mofox_order": order,
    }


def run_request(plugin, stream_id: str) -> list[LLMPayload]:
    handler = TavernRequestHandler(plugin)
    payloads = [
        LLMPayload(ROLE.SYSTEM, [Text("系统提示词")]),
        LLMPayload(ROLE.TOOL, [Text("工具占位")]),
        LLMPayload(ROLE.USER, [Text("用户输入")]),
    ]
    params = {
        "request_name": "neo_default_chatter",
        "payloads": payloads,
        "meta_data": {"stream_id": stream_id},
    }
    asyncio.run(handler.execute("before_llm_request", params))
    return params["payloads"]


def real_like_payloads() -> list[LLMPayload]:
    """模拟第二次请求的真实结构（与用户日志一致）。

    System(系统提示) → User(系统上下文/历史) → Assistant(上轮回复, 带 tool_call)
    → Tool(工具结果) → Assistant(__SUSPEND__) → User(本轮新输入)
    """
    return [
        LLMPayload(ROLE.SYSTEM, [Text("MoFox 系统提示词")]),
        LLMPayload(ROLE.TOOL, [Text("工具声明")]),
        LLMPayload(
            ROLE.USER, [Text("系统上下文：conversation_context + latest_events")]
        ),
        LLMPayload(
            ROLE.ASSISTANT,
            [
                Text("上轮回复"),
                ToolCall(
                    "call_1", "action-send_text", {"content": "艾特我又半天不放一个字"}
                ),
            ],
        ),
        LLMPayload(ROLE.TOOL_RESULT, [ToolResult({"status": "已发送消息"}, "call_1")]),
        LLMPayload(ROLE.ASSISTANT, [Text("__SUSPEND__")]),
        LLMPayload(ROLE.USER, [Text("本轮新输入：latest_events")]),
    ]


def run_request_with(
    plugin, payloads: list[LLMPayload], stream_id: str = "s"
) -> list[LLMPayload]:
    handler = TavernRequestHandler(plugin)
    params = {
        "request_name": "neo_default_chatter",
        "payloads": payloads,
        "meta_data": {"stream_id": stream_id},
    }
    asyncio.run(handler.execute("before_llm_request", params))
    return params["payloads"]


def convo_signature(payloads: list[LLMPayload]) -> list[str]:
    """取「系统上下文 + 上轮回复 + 工具调用 + 本轮新输入」这一段的角色序列。"""
    roles = [str(payload.role) for payload in payloads]
    try:
        start = roles.index(str(ROLE.USER))
    except ValueError:
        return []
    # 从第一条非 system/tool 的 user 开始，取到末尾的对话部分
    return [
        role
        for role in roles[start:]
        if role in (str(ROLE.USER), str(ROLE.ASSISTANT), str(ROLE.TOOL_RESULT))
    ]


def text_of(payloads: list[LLMPayload]) -> str:
    return "\n".join(
        part.text
        for payload in payloads
        for part in payload.content
        if isinstance(part, Text)
    )


def test_request_injects_novel_and_advances(tmp_path: Path) -> None:
    write_novel(tmp_path)
    (tmp_path / "setvar.json").write_text(
        json.dumps(
            setvar_payload(
                ["mofox_system", "reader", "novel_current", "mofox_tool", "mofox_user"]
            ),
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    plugin = make_plugin(tmp_path, enabled=True, variable_name="current_chapter")

    first = run_request(plugin, "s1")
    joined = text_of(first)
    assert "第一章" in joined
    assert "<novel>第一章" in joined

    second = run_request(plugin, "s1")
    assert "第二章" in text_of(second)

    # 另一条聊天流进度独立
    other = run_request(plugin, "s2")
    assert "第一章" in text_of(other)


def test_request_skips_novel_when_disabled(tmp_path: Path) -> None:
    write_novel(tmp_path)
    (tmp_path / "setvar.json").write_text(
        json.dumps(
            setvar_payload(["mofox_system", "reader", "novel_current", "mofox_user"]),
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    plugin = make_plugin(tmp_path, enabled=False)
    payloads = run_request(plugin, "s1")
    joined = text_of(payloads)
    assert "第一章" not in joined
    assert "<novel></novel>" in joined


def test_request_entry_disabled_still_seeds_variable(tmp_path: Path) -> None:
    write_novel(tmp_path)
    (tmp_path / "setvar.json").write_text(
        json.dumps(
            setvar_payload(["mofox_system", "reader", "novel_current", "mofox_user"]),
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    plugin = make_plugin(tmp_path, enabled=True, entry_enabled=False)
    joined = text_of(run_request(plugin, "s1"))
    # 条目本身不注入，但变量仍然被填充
    assert "<novel>第一章" in joined
    assert joined.count("第一章") == 1


def test_request_respects_custom_variable_name(tmp_path: Path) -> None:
    write_novel(tmp_path)
    (tmp_path / "setvar.json").write_text(
        json.dumps(
            setvar_payload(["mofox_system", "reader", "novel_current", "mofox_user"]),
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    plugin = make_plugin(tmp_path, enabled=True, variable_name="my_chapter")
    # reader 读的是 current_chapter，此时应该读不到内容
    joined = text_of(run_request(plugin, "s1"))
    assert "<novel></novel>" in joined
    # 但小说条目自身仍然注入
    assert "第一章" in joined


# ----- WebUI 接口 -----


def test_ordered_items_include_readonly_entries(tmp_path: Path) -> None:
    """WebUI 列表必须能同时列出三块固定内容与小说动态条目。"""
    write_novel(tmp_path)
    service = make_service(tmp_path, enabled=True)

    fixed = service.fixed_prompt_items()
    assert [item["identifier"] for item in fixed] == [
        "mofox_system",
        "mofox_tool",
        "mofox_user",
    ]

    ordered = service.list_ordered_prompt_items()
    identifiers = [item["identifier"] for item in ordered]
    assert "novel_current" in identifiers
    assert "mofox_system" in identifiers
    novel_item = next(item for item in ordered if item["identifier"] == "novel_current")
    assert novel_item["novel"] is True
    assert novel_item["fixed"] is True
    assert novel_item["content"] == "{{getvar::current_chapter}}"


def test_request_injects_novel_with_configured_role(tmp_path: Path) -> None:
    """配置里的 role 必须决定注入 payload 的角色，而不是固定 system。"""
    write_novel(tmp_path)
    (tmp_path / "setvar.json").write_text(
        json.dumps(
            setvar_payload(["mofox_system", "reader", "mofox_user", "novel_current"]),
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    for role, expected in (
        ("system", ROLE.SYSTEM),
        ("user", ROLE.USER),
        ("assistant", ROLE.ASSISTANT),
    ):
        plugin = make_plugin(tmp_path, enabled=True, role=role)
        payloads = run_request(plugin, f"s-{role}")
        novel_payloads = []
        for payload in payloads:
            text = "".join(
                part.text for part in payload.content if isinstance(part, Text)
            )
            # 小说条目自身：正文里没有 <novel> 包裹（那是预设里的 reader 条目）
            if "tavern_setvar" in text and "第一章" in text and "<novel>" not in text:
                novel_payloads.append(payload)
        assert novel_payloads, f"{role}: 没找到小说条目"
        assert novel_payloads[0].role == expected, f"{role} -> {novel_payloads[0].role}"


def test_webui_state_endpoint_ok(tmp_path: Path) -> None:
    """GET /api/state 必须返回 200（曾因 fixed_prompt_items 的 KeyError 报 500）。"""
    write_novel(tmp_path)
    service = make_service(tmp_path, enabled=True)
    (tmp_path / "setvar.json").write_text(
        json.dumps(
            setvar_payload(["mofox_system", "novel_current", "mofox_user"]),
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    router_module = importlib.import_module("tavern_preset_regex.router")
    config = TavernRegexConfig()
    config.plugin.data_dir = str(tmp_path)
    config.novel = NovelSection(enabled=True)
    plugin = type("_FakePlugin", (), {"config": config, "plugin_name": "t"})()
    router = router_module.TavernRegexAdminRouter(plugin)

    from fastapi.testclient import TestClient

    client = TestClient(router.app, raise_server_exceptions=False)
    for path in ("/api/state", "/api/novel", "/api/novel?stream_id=x"):
        response = client.get(path)
        assert response.status_code == 200, f"{path} -> {response.status_code}"
    assert service.novel_state("x")["total"] == 3


# ----- 对话块位置（user_block_position）-----


def prepare_block_fixture(tmp_path: Path) -> None:
    write_novel(tmp_path)
    (tmp_path / "setvar.json").write_text(
        json.dumps(
            {
                "prompts": [
                    {
                        "identifier": "prefill",
                        "name": "ass预设",
                        "role": "assistant",
                        "content": "明白了。",
                        "enabled": True,
                    },
                    {
                        "identifier": "tail",
                        "name": "自定义user",
                        "role": "user",
                        "content": "然后直接开始输出",
                        "enabled": True,
                    },
                ],
                "prompt_order": [
                    {"identifier": "prefill", "enabled": True},
                    {"identifier": "tail", "enabled": True},
                ],
                "mofox_order": [
                    "mofox_system",
                    "prefill",
                    "tail",
                    "mofox_tool",
                    "mofox_user",
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )


def test_conversation_block_stays_contiguous(tmp_path: Path) -> None:
    """对话块内部的 历史→回复→工具→新输入 必须保持连续、不被打散。"""
    prepare_block_fixture(tmp_path)
    plugin = make_plugin(tmp_path, enabled=False)
    payloads = run_request_with(plugin, real_like_payloads())

    roles = [str(payload.role) for payload in payloads]
    # 对话块的角色序列应当连续出现
    convo = [
        str(ROLE.USER),
        str(ROLE.ASSISTANT),
        str(ROLE.TOOL_RESULT),
        str(ROLE.ASSISTANT),
        str(ROLE.USER),
    ]
    joined = ",".join(roles)
    assert ",".join(convo) in joined, f"对话块被打散：{roles}"

    # 工具调用记录仍在 assistant 上，且紧跟系统上下文
    assistant_with_tools = [
        payload
        for payload in payloads
        if payload.role == ROLE.ASSISTANT
        and any(isinstance(part, ToolCall) for part in payload.content)
    ]
    assert assistant_with_tools, "带工具调用的 assistant 丢了"


def test_user_block_position_after_system(tmp_path: Path) -> None:
    """after_system：对话块紧跟系统提示词，排在所有预设之前。"""
    prepare_block_fixture(tmp_path)
    plugin = make_plugin(tmp_path, enabled=False)
    plugin.config.plugin.user_block_position = "after_system"

    payloads = run_request_with(plugin, real_like_payloads())
    roles = [str(payload.role) for payload in payloads]

    # 对话块（历史 + 上轮回复 + 工具调用 + 本轮新输入）必须连续出现
    convo = [
        str(ROLE.USER),
        str(ROLE.ASSISTANT),
        str(ROLE.TOOL_RESULT),
        str(ROLE.ASSISTANT),
        str(ROLE.USER),
    ]
    joined = ",".join(roles)
    assert ",".join(convo) in joined, f"对话块被打散：{roles}"

    # after_system：对话块整体连续出现，内部顺序为 历史→回复→工具→新输入
    texts = [
        "".join(part.text for part in payload.content if isinstance(part, Text))
        for payload in payloads
    ]
    convo_start = next(
        index for index, text in enumerate(texts) if "系统上下文" in text
    )
    assert roles[convo_start : convo_start + 5] == convo, f"对话块顺序不对：{roles}"
    assert "本轮新输入" in texts[convo_start + 4]


def test_user_block_position_end(tmp_path: Path) -> None:
    """end：对话块固定放在最后。"""
    prepare_block_fixture(tmp_path)
    plugin = make_plugin(tmp_path, enabled=False)
    plugin.config.plugin.user_block_position = "end"

    payloads = run_request_with(plugin, real_like_payloads())
    assert str(payloads[-1].role) == str(ROLE.USER)
    assert "本轮新输入" in "".join(
        part.text for part in payloads[-1].content if isinstance(part, Text)
    )


def test_user_block_position_defaults_to_auto(tmp_path: Path) -> None:
    """默认 auto：不改动既有顺序表行为。"""
    prepare_block_fixture(tmp_path)
    plugin = make_plugin(tmp_path, enabled=False)
    payloads = run_request_with(plugin, real_like_payloads())
    roles = [str(payload.role) for payload in payloads]
    # mofox_order 里 mofox_user 在最后，所以最后仍是 user
    assert roles[-1] == str(ROLE.USER)
    assert "本轮新输入" in "".join(
        part.text for part in payloads[-1].content if isinstance(part, Text)
    )


def test_block_position_saved_via_api(tmp_path: Path) -> None:
    """WebUI 保存的对话块位置要写进 novel/config.json 并生效。"""
    prepare_block_fixture(tmp_path)
    service = make_service(tmp_path, enabled=False)
    assert service.novel_settings()["user_block_position"] == "auto"

    service.save_novel_settings({"user_block_position": "after_system"})
    stored = json.loads((tmp_path / "novel" / "config.json").read_text("utf-8"))
    assert stored == {"user_block_position": "after_system"}
    assert service.novel_settings()["user_block_position"] == "after_system"

    # TOML 里改的值会被 config.json 覆盖（UI 改动优先）
    config = TavernRegexConfig()
    config.plugin.data_dir = str(tmp_path)
    config.plugin.user_block_position = "end"
    plugin = type("_FakePlugin", (), {"config": config})()
    service2 = TavernDataService(tavern_dir=tmp_path, plugin=plugin)
    assert service2.novel_settings()["user_block_position"] == "after_system"


# ----- {{mofox_conversation}} 宏 -----


def prepare_conversation_fixture(tmp_path: Path) -> None:
    write_novel(tmp_path)
    (tmp_path / "setvar.json").write_text(
        json.dumps(
            {
                "prompts": [
                    {
                        "identifier": "ctx",
                        "name": "上下文条目",
                        "role": "user",
                        "content": "当前对话：\n{{mofox_conversation}}",
                        "enabled": True,
                    }
                ],
                "prompt_order": [{"identifier": "ctx", "enabled": True}],
                "mofox_order": ["mofox_system", "ctx", "mofox_user"],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )


def test_conversation_macro_contains_tool_calls(tmp_path: Path) -> None:
    """预设条目里的 {{mofox_conversation}} 要包含回复、工具调用与工具结果。"""
    prepare_conversation_fixture(tmp_path)
    plugin = make_plugin(tmp_path, enabled=False)
    payloads = run_request_with(plugin, real_like_payloads())

    rendered = [
        "".join(part.text for part in payload.content if isinstance(part, Text))
        for payload in payloads
        if payload.role == ROLE.USER
    ]
    joined = "\n".join(rendered)
    assert "当前对话：" in joined
    assert "系统上下文" in joined  # 历史
    assert "上轮回复" in joined  # 上一轮回复
    assert "调用工具 action-send_text" in joined  # 工具调用
    assert "已发送消息" in joined  # 工具结果
    assert "本轮新输入" in joined  # 本轮新消息


def test_conversation_macro_empty_when_no_conversation(tmp_path: Path) -> None:
    """没有对话内容时宏渲染为空，不应残留占位符。"""
    prepare_conversation_fixture(tmp_path)
    plugin = make_plugin(tmp_path, enabled=False)
    payloads = [
        LLMPayload(ROLE.SYSTEM, [Text("系统提示词")]),
        LLMPayload(ROLE.TOOL, [Text("工具声明")]),
    ]
    result = run_request_with(plugin, payloads)
    joined = "\n".join(
        "".join(part.text for part in payload.content if isinstance(part, Text))
        for payload in result
    )
    assert "{{mofox_conversation}}" not in joined
