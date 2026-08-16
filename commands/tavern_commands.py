"""SillyTavern 兼容的 setvar 与 regex 管理命令。"""

from __future__ import annotations

import json
from typing import Any

from src.app.plugin_system.api.log_api import get_logger
from src.app.plugin_system.api.send_api import send_text
from src.app.plugin_system.base import BaseCommand, cmd_route
from src.app.plugin_system.types import PermissionLevel

from ..tavern_store import TavernDataService

logger = get_logger("tavern_preset_regex.tavern_commands")


def _preview(value: str, limit: int = 120) -> str:
    """生成适合命令回复的单行预览。"""
    value = (value or "").replace("\r", "").replace("\n", " ")
    if len(value) > limit:
        return value[: limit - 1] + "…"
    return value


def _bool_text(value: Any) -> str:
    if isinstance(value, str):
        return value.lower() in ("1", "true", "yes", "on")
    return bool(value)


class TavernSetvarCommand(BaseCommand):
    """读取、编辑并注入 data/tavern_preset_regex/setvar.json。"""

    name: str = "setvar"
    description: str = "管理 data/tavern_preset_regex/setvar.json 并注入 SillyTavern 变量预设"
    permission_level: PermissionLevel = PermissionLevel.OWNER

    _USAGE = """/setvar 用法：
  /setvar                 - 显示变量状态与帮助
  /setvar list            - 列出当前解析出的变量
  /setvar get <名称>      - 读取一个变量的值
  /setvar set <名称> <值> - 编辑/新增变量（值建议用引号包住）
  /setvar clear <名称>    - 清空变量
  /setvar apply           - 重新读取并注入全部启用提示词
  /setvar prompts         - 按注入顺序列出提示词开关状态
  /setvar enable <编号|名称|id>  - 启用提示词
  /setvar disable <编号|名称|id> - 禁用提示词（编号即注入顺序）"""

    @classmethod
    def match(cls, parts: list[str]) -> int:
        if parts and parts[0] in ("setvar", "变量", "var"):
            return 1
        return 0

    def _service(self) -> TavernDataService:
        return TavernDataService(plugin=self.plugin)

    async def _reply(self, text: str) -> None:
        await send_text(text, stream_id=self.stream_id)

    @cmd_route()
    async def handle_default(self) -> tuple[bool, str]:
        try:
            variables = self._service().resolve_variables()
            text = (
                f"{self._USAGE}\n\n"
                f"当前解析出 {len(variables)} 个变量，"
                f"启用提示词 {len([p for p in self._service().list_prompts() if p.get('enabled', True)])} 条。"
            )
        except Exception as exc:
            text = f"{self._USAGE}\n\n读取 setvar.json 失败：{exc}"
        await self._reply(text)
        return True, text

    @cmd_route("list")
    async def handle_list(self) -> tuple[bool, str]:
        try:
            variables = self._service().resolve_variables()
            if not variables:
                text = "当前没有解析出变量。"
            else:
                lines = [f"共 {len(variables)} 个变量："]
                for name, value in variables.items():
                    lines.append(f"- {name}: {_preview(value)}")
                text = "\n".join(lines)
        except Exception as exc:
            text = f"读取变量失败：{exc}"
        await self._reply(text)
        return True, text

    @cmd_route("get")
    async def handle_get(self, name: str) -> tuple[bool, str]:
        try:
            value = self._service().get_variable(name)
            text = f"{name} = {value}" if value else f"{name} 为空或未定义。"
        except Exception as exc:
            text = f"读取变量失败：{exc}"
        await self._reply(text)
        return True, text

    @cmd_route("set")
    async def handle_set(self, name: str, value: str) -> tuple[bool, str]:
        try:
            self._service().set_variable(name, value)
            self._service().render_setvar_prompt()
            text = f"已设置变量 {name}，将在下一次主回复请求中生效。"
        except Exception as exc:
            text = f"设置变量失败：{exc}"
        await self._reply(text)
        return True, text

    @cmd_route("clear")
    async def handle_clear(self, name: str) -> tuple[bool, str]:
        try:
            self._service().clear_variable(name)
            self._service().render_setvar_prompt()
            text = f"已清空变量 {name}，将在下一次主回复请求中生效。"
        except Exception as exc:
            text = f"清空变量失败：{exc}"
        await self._reply(text)
        return True, text

    @cmd_route("apply")
    @cmd_route("reload")
    @cmd_route("注入")
    async def handle_apply(self) -> tuple[bool, str]:
        try:
            self._service().render_setvar_prompt()
            text = "已重新加载 setvar.json，下一次主回复请求会注入启用提示词。"
        except Exception as exc:
            text = f"注入失败：{exc}"
        await self._reply(text)
        return True, text

    @cmd_route("prompts")
    @cmd_route("提示词")
    async def handle_prompts(self) -> tuple[bool, str]:
        try:
            prompts = self._service().list_prompts()
            lines = [f"setvar.json 中共 {len(prompts)} 条提示词："]
            for index, prompt in enumerate(prompts, start=1):
                enabled = "启用" if bool(prompt.get("enabled", True)) else "停用"
                name = prompt.get("name") or "(未命名)"
                lines.append(f"{index}. [{enabled}] {name}")
            text = "\n".join(lines)
        except Exception as exc:
            text = f"读取提示词失败：{exc}"
        await self._reply(text)
        return True, text

    @cmd_route("enable")
    @cmd_route("启用")
    async def handle_enable(self, key: str) -> tuple[bool, str]:
        try:
            prompt = self._service().set_prompt_enabled(key, True)
            self._service().render_setvar_prompt()
            text = f"已启用提示词：{prompt.get('name') or key}"
        except Exception as exc:
            text = f"启用提示词失败：{exc}"
        await self._reply(text)
        return True, text

    @cmd_route("disable")
    @cmd_route("禁用")
    async def handle_disable(self, key: str) -> tuple[bool, str]:
        try:
            prompt = self._service().set_prompt_enabled(key, False)
            self._service().render_setvar_prompt()
            text = f"已禁用提示词：{prompt.get('name') or key}"
        except Exception as exc:
            text = f"禁用提示词失败：{exc}"
        await self._reply(text)
        return True, text


class TavernRegexCommand(BaseCommand):
    """读取、编辑并应用 data/tavern_preset_regex/regex.json。"""

    name: str = "regex"
    description: str = "管理 data/tavern_preset_regex/regex.json 并在消息收发时应用正则"
    permission_level: PermissionLevel = PermissionLevel.OWNER

    _USAGE = """/regex 用法：
  /regex                       - 显示规则状态与帮助
  /regex list                  - 列出规则
  /regex show <编号|名称|id>   - 查看规则详情
  /regex enable <编号|名称|id> - 启用规则
  /regex disable <编号|名称|id> - 禁用规则
  /regex set <编号|名称|id> <字段> <值>
    字段：name/pattern/replacement/markdown/prompt/placement
    示例：/regex set 3 pattern "/你好/g"
          /regex set 3 replacement "您好" """

    @classmethod
    def match(cls, parts: list[str]) -> int:
        if parts and parts[0] in ("regex", "正则", "regexp"):
            return 1
        return 0

    def _service(self) -> TavernDataService:
        return TavernDataService(plugin=self.plugin)

    async def _reply(self, text: str) -> None:
        await send_text(text, stream_id=self.stream_id)

    def _format_entry(self, entry: dict[str, Any], index: int) -> str:
        enabled = "停用" if entry.get("disabled") else "启用"
        markdown = "显示" if entry.get("markdownOnly") else "-"
        prompt = "发送" if entry.get("promptOnly") else "-"
        placement = ",".join(str(x) for x in (entry.get("placement") or []))
        pattern = _preview(str(entry.get("findRegex", "")))
        return (
            f"{index}. [{enabled}|显示:{markdown}|发送:{prompt}|placement:{placement}] "
            f"{entry.get('scriptName') or entry.get('id')}\n"
            f"   {pattern}"
        )

    @cmd_route()
    async def handle_default(self) -> tuple[bool, str]:
        try:
            entries = self._service().load_regex_entries()
            enabled_count = sum(1 for item in entries if not item.get("disabled"))
            text = (
                f"{self._USAGE}\n\n"
                f"regex.json 共 {len(entries)} 条规则，启用 {enabled_count} 条。"
            )
        except Exception as exc:
            text = f"{self._USAGE}\n\n读取 regex.json 失败：{exc}"
        await self._reply(text)
        return True, text

    @cmd_route("list")
    async def handle_list(self) -> tuple[bool, str]:
        try:
            entries = self._service().load_regex_entries()
            if not entries:
                text = "regex.json 中还没有规则。"
            else:
                lines = [f"共 {len(entries)} 条规则："]
                for index, entry in enumerate(entries, start=1):
                    lines.append(self._format_entry(entry, index))
                text = "\n".join(lines)
        except Exception as exc:
            text = f"读取正则失败：{exc}"
        await self._reply(text)
        return True, text

    @cmd_route("show")
    @cmd_route("查看")
    async def handle_show(self, key: str) -> tuple[bool, str]:
        try:
            entry, index = self._service().find_regex_entry(key)
            text = json.dumps(entry, ensure_ascii=False, indent=2)
        except Exception as exc:
            text = f"查看规则失败：{exc}"
        await self._reply(text)
        return True, text

    @cmd_route("enable")
    @cmd_route("启用")
    async def handle_enable(self, key: str) -> tuple[bool, str]:
        try:
            entry = self._service().set_regex_enabled(key, True)
            text = f"已启用规则：{entry.get('scriptName') or entry.get('id')}"
        except Exception as exc:
            text = f"启用规则失败：{exc}"
        await self._reply(text)
        return True, text

    @cmd_route("disable")
    @cmd_route("禁用")
    async def handle_disable(self, key: str) -> tuple[bool, str]:
        try:
            entry = self._service().set_regex_enabled(key, False)
            text = f"已禁用规则：{entry.get('scriptName') or entry.get('id')}"
        except Exception as exc:
            text = f"禁用规则失败：{exc}"
        await self._reply(text)
        return True, text

    @cmd_route("set")
    async def handle_set(self, key: str, field: str, value: str) -> tuple[bool, str]:
        try:
            entry: dict[str, Any] | None = None
            normalized = field.lower()
            if normalized in ("name", "scriptname"):
                entry = self._service().update_regex_entry(key, scriptName=value)
            elif normalized in ("pattern", "find", "findregex", "find_regex"):
                entry = self._service().update_regex_entry(key, findRegex=value)
            elif normalized in ("replacement", "replace", "replacestring", "replace_string"):
                entry = self._service().update_regex_entry(key, replaceString=value)
            elif normalized in ("markdown", "markdownonly"):
                entry = self._service().update_regex_entry(
                    key,
                    markdownOnly=_bool_text(value),
                )
            elif normalized in ("prompt", "promptonly"):
                entry = self._service().update_regex_entry(
                    key,
                    promptOnly=_bool_text(value),
                )
            elif normalized in ("placement", "target"):
                placement = [
                    int(item.strip())
                    for item in value.split(",")
                    if item.strip().isdigit()
                ]
                entry = self._service().update_regex_entry(key, placement=placement)
            else:
                text = f"不支持的字段: {field}"
                await self._reply(text)
                return False, text
            text = f"已更新规则：{entry.get('scriptName') or entry.get('id')}"
        except Exception as exc:
            text = f"更新规则失败：{exc}"
        await self._reply(text)
        return True, text
