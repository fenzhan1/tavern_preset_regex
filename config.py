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
            description=(
                "用户黑名单；命中后跳过。格式同 user_whitelist。"
            ),
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
            description=(
                "群聊黑名单；命中后跳过。格式同 group_whitelist。"
            ),
            input_type="list",
            item_type="str",
        )
        debug_log: bool = Field(
            default=False,
            description="替换发生时输出 INFO 日志，便于调试规则",
        )

    plugin: PluginSection = Field(default_factory=PluginSection)
