"""tavern_preset_regex 插件配置模型。"""

from __future__ import annotations

from typing import ClassVar, Literal

from src.app.plugin_system.base import BaseConfig, Field, SectionBase, config_section


class RuleSection(SectionBase):
    """从 SillyTavern regex.json 转换出的单条规则。"""

    name: str = Field(default="", description="规则名称")
    enabled: bool = Field(default=True, description="是否启用该规则")
    target: Literal["input", "output", "both"] = Field(
        default="both",
        description="input 作用于主回复模型请求，output 作用于主回复模型结果",
    )
    pattern: str = Field(default="", description="Python 正则表达式")
    replacement: str = Field(default="", description="替换文本")
    flags: str = Field(default="", description="正则标志字符串")
    description: str = Field(default="", description="规则说明")


class NovelSection(SectionBase):
    """小说分段自动注入配置。"""

    enabled: bool = Field(
        default=False,
        description=(
            "启用小说自动注入。\n"
            "把小说放进 data/tavern_preset_regex/novel/，开启后每次主回复请求\n"
            "自动注入当前段落并推进进度。"
        ),
    )
    file: str = Field(
        default="",
        description=(
            "当前小说文件名（novel/ 目录下的 .txt / .md）。\n"
            "留空时自动使用目录里的第一个小说文件。"
        ),
    )
    split_mode: Literal["auto", "chapter", "char", "line"] = Field(
        default="auto",
        description=(
            "分段方式：auto 自动识别章节标题（识别不到按字数）、chapter 只按章节、\n"
            "char 按字数、line 按行数。"
        ),
        choices=["auto", "chapter", "char", "line"],
    )
    char_size: int = Field(
        default=10000,
        description="按字数分段时每段字数",
    )
    lines_per_segment: int = Field(
        default=60,
        description="按行数分段时每段行数",
    )
    chapter_pattern: str = Field(
        default="",
        description=(
            "自定义章节标题正则（可留空）。\n"
            "留空时使用内置识别：第X章/回/卷、序章/楔子/番外、数字序号、中文序号。"
        ),
    )
    batch_size: int = Field(
        default=1,
        description="每次注入段数，填 3 就把 3 段拼在一起注入并一次跳 3 段",
    )
    loop: bool = Field(
        default=False,
        description="读到最后一段后，下一次从第一段重新开始",
    )
    variable_name: str = Field(
        default="current_chapter",
        description=(
            "当前段落写入的变量名。\n"
            "在预设里写 {{getvar::current_chapter}} 也能读到同样的内容。"
        ),
    )
    role: Literal["system", "user", "assistant"] = Field(
        default="system",
        description=(
            "「📖小说当前段落」注入请求时使用的角色。\n"
            "也可在 WebUI「小说」标签的「注入角色」里改，改完立即生效。"
        ),
        choices=["system", "user", "assistant"],
    )
    entry_enabled: bool = Field(
        default=True,
        description="是否在请求中注入「📖小说当前段落」条目（关闭后仍会写入变量）",
    )
    inject_when_empty: bool = Field(
        default=False,
        description="小说读完且未开启循环时，是否仍注入空的条目",
    )


class TavernRegexConfig(BaseConfig):
    """tavern_preset_regex 插件配置模型。"""

    name: ClassVar[str] = "config"
    description: ClassVar[str] = "酒馆兼容 setvar / regex 插件配置"

    @config_section("plugin", title="插件开关", tag="plugin")
    class PluginSection(SectionBase):
        """插件总开关与主回复模型过滤规则。"""

        enabled: bool = Field(
            default=True,
            description="启用插件",
        )
        data_dir: str = Field(
            default="data/tavern_preset_regex",
            description=(
                "存放 setvar.json 与 regex.json 的目录。\n"
                "可填写绝对路径，或相对项目根目录的路径。"
            ),
        )
        inject_setvar: bool = Field(
            default=True,
            description="是否把 setvar.json 注入主回复模型请求",
        )
        use_tavern_preset_regex: bool = Field(
            default=True,
            description="是否对主回复模型请求/结果应用 regex.json",
        )
        main_request_names: list[str] = Field(
            default_factory=lambda: ["default_chatter", "neo_default_chatter"],
            description=(
                "只处理这些 LLM request_name；留空时使用默认主回复 chatter 名称。"
            ),
        )
        user_block_position: Literal[
            "auto", "before_last_user", "after_system", "end"
        ] = Field(
            default="auto",
            description=(
                "「MoFox 用户上下文」这一块（历史消息 + 上一轮回复 + 工具调用 + "
                "本轮新输入）放在请求的什么位置。\n"
                "auto：完全按 WebUI 顺序表（mofox_order）里拖到的位置；\n"
                "before_last_user：预设条目排在「历史 + 本轮新输入」之后——"
                "历史/上轮回复/工具调用 → 本轮新输入 → 预设条目；\n"
                "after_system：紧跟系统提示词之后、所有酒馆预设之前；\n"
                "end：固定放在请求最后。"
            ),
            choices=["auto", "before_last_user", "after_system", "end"],
        )
        filter_mode: Literal["disabled", "blacklist", "whitelist"] = Field(
            default="disabled",
            description="聊天流白名单/黑名单模式",
            choices=["disabled", "blacklist", "whitelist"],
        )
        user_whitelist: list[str] = Field(
            default_factory=list,
            description=(
                "用户白名单；filter_mode=whitelist 时仅处理这些私聊。\n"
                '格式："user:*" 或 "user:QQ号"'
            ),
            input_type="list",
            item_type="str",
        )
        user_blacklist: list[str] = Field(
            default_factory=list,
            description=("用户黑名单；命中后跳过。格式同 user_whitelist。"),
            input_type="list",
            item_type="str",
        )
        group_whitelist: list[str] = Field(
            default_factory=list,
            description=(
                "群聊白名单；filter_mode=whitelist 时仅处理这些群。\n"
                '格式："group:*" 或 "group:群号"'
            ),
            input_type="list",
            item_type="str",
        )
        group_blacklist: list[str] = Field(
            default_factory=list,
            description=("群聊黑名单；命中后跳过。格式同 group_whitelist。"),
            input_type="list",
            item_type="str",
        )
        debug_log: bool = Field(
            default=False,
            description="替换发生时输出 INFO 日志，便于调试规则",
        )

    plugin: PluginSection = Field(default_factory=PluginSection)
    novel: NovelSection = Field(default_factory=NovelSection)
