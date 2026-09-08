"""tavern_preset_regex 插件入口。"""

from __future__ import annotations

from src.app.plugin_system.base import BasePlugin, register_plugin

from .commands.tavern_commands import (
    TavernNovelCommand,
    TavernRegexCommand,
    TavernSetvarCommand,
)
from .config import TavernRegexConfig
from .event_handler import TavernRequestHandler, TavernResponseHandler
from .router import TavernRegexAdminRouter


@register_plugin
class TavernRegexPlugin(BasePlugin):
    """只保留 SillyTavern 兼容的 setvar 与 regex 两个能力。"""

    plugin_name: str = "tavern_preset_regex"
    plugin_description: str = (
        "直接读写 data/tavern_preset_regex 的 setvar、regex 与 novel，"
        "仅作用于发送给主回复模型的请求与结果"
    )
    plugin_version: str = "2.4.4"

    configs: list[type] = [TavernRegexConfig]

    async def on_plugin_unloaded(self) -> None:
        """卸载时清掉配置缓存，确保插件设置热重载生效。"""
        try:
            from src.app.plugin_system.api.config_api import remove_config

            remove_config(self.plugin_name)
        except Exception:
            pass

    def get_components(self) -> list[type]:
        config = self.config
        if isinstance(config, TavernRegexConfig) and not config.plugin.enabled:
            return []
        return [
            TavernRequestHandler,
            TavernResponseHandler,
            TavernSetvarCommand,
            TavernRegexCommand,
            TavernNovelCommand,
            TavernRegexAdminRouter,
        ]
