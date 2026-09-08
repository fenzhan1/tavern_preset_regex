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
    description: str = (
        "管理 data/tavern_preset_regex/setvar.json 并注入 SillyTavern 变量预设"
    )
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

    async def execute(self, message_text: str) -> tuple[bool, str]:
        """显式桥接到 BaseCommand 的 Trie 路由执行。"""
        return await super().execute(message_text)

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


class TavernNovelCommand(BaseCommand):
    """控制 data/tavern_preset_regex/novel 下小说的分段注入进度。"""

    name: str = "novel"
    description: str = "查看并控制小说分段注入的进度与设置"
    permission_level: PermissionLevel = PermissionLevel.OWNER

    _USAGE = """/novel 用法：
  /novel                     - 显示小说状态与帮助
  /novel status              - 当前小说、分段方式与进度
  /novel next                - 手动推进并注入下一段
  /novel jump <段号>         - 跳转到指定段并注入
  /novel reset               - 进度重置到第一段
  /novel on / off            - 启用 / 关闭小说自动注入
  /novel list                - 列出 novel/ 目录下的小说文件
  /novel file <文件名>       - 切换当前小说
  /novel split <模式>        - 分段方式：auto/chapter/char/line
  /novel batch <段数>        - 每次注入段数
  /novel loop <on|off>       - 读完后是否循环
  /novel var <变量名>        - 当前段落写入的变量名"""

    @classmethod
    def match(cls, parts: list[str]) -> int:
        if parts and parts[0] in ("novel", "小说"):
            return 1
        return 0

    def _service(self) -> TavernDataService:
        return TavernDataService(plugin=self.plugin)

    async def execute(self, message_text: str) -> tuple[bool, str]:
        """显式桥接到 BaseCommand 的 Trie 路由执行。"""
        return await super().execute(message_text)

    async def _reply(self, text: str) -> None:
        await send_text(text, stream_id=self.stream_id)

    def _status_text(self) -> str:
        service = self._service()
        state = service.novel_state(self.stream_id)
        config = state.get("config", {})
        if state.get("error"):
            return f"读取小说失败：{state['error']}"
        if not state.get("active_file"):
            return (
                f"novel/ 目录里还没有小说文件。\n目录：{state.get('dir')}\n"
                "把 .txt / .md 小说放进去，然后用 /novel 重新查看。"
            )
        total = state.get("total", 0)
        index = state.get("index", 0)
        progress = f"{min(index + 1, total)}/{total}" if total else "0/0"
        return (
            f"小说：{state['active_file']}\n"
            f"状态：{'已启用' if config.get('enabled') else '未启用'}\n"
            f"分段：{state.get('label') or config.get('split_mode')}"
            f"（{total} 段，全文 {state.get('chars')} 字）\n"
            f"进度：第 {progress} 段\n"
            f"变量：{config.get('variable_name')}｜角色：{config.get('role')}\n"
            f"每次注入 {config.get('batch_size')} 段｜循环：{'开' if config.get('loop') else '关'}"
        )

    @cmd_route()
    async def handle_default(self) -> tuple[bool, str]:
        try:
            text = f"{self._status_text()}\n\n{self._USAGE}"
        except Exception as exc:
            text = f"读取小说状态失败：{exc}"
        await self._reply(text)
        return True, text

    @cmd_route("status")
    @cmd_route("状态")
    async def handle_status(self) -> tuple[bool, str]:
        try:
            text = self._status_text()
        except Exception as exc:
            text = f"读取小说状态失败：{exc}"
        await self._reply(text)
        return True, text

    @cmd_route("list")
    @cmd_route("列表")
    async def handle_list(self) -> tuple[bool, str]:
        try:
            state = self._service().novel_state(self.stream_id)
            files = state.get("files", [])
            if not files:
                text = f"novel/ 目录里没有小说文件：{state.get('dir')}"
            else:
                lines = [f"novel/ 目录下共 {len(files)} 个小说文件："]
                for index, item in enumerate(files, start=1):
                    mark = "（当前）" if item.get("name") == state.get("active_file") else ""
                    lines.append(
                        f"{index}. {item.get('name')} - "
                        f"{round(item.get('size', 0) / 1024)} KB{mark}"
                    )
                text = "\n".join(lines)
        except Exception as exc:
            text = f"列出小说失败：{exc}"
        await self._reply(text)
        return True, text

    @cmd_route("next")
    @cmd_route("推进")
    async def handle_next(self) -> tuple[bool, str]:
        try:
            result = self._service().advance_novel(self.stream_id)
            if not result.get("start"):
                text = (
                    "小说已读到最后一" + ("段" if result.get("total") else "页")
                    if result.get("total")
                    else "novel/ 目录下没有可用的小说"
                )
            else:
                tail = "，已到末尾" if result.get("finished") else ""
                text = (
                    f"已注入第 {result['start']} ~ {result['end']} 段"
                    f"（共 {result['total']} 段）{tail}"
                )
        except Exception as exc:
            text = f"推进小说失败：{exc}"
        await self._reply(text)
        return True, text

    @cmd_route("jump")
    @cmd_route("跳转")
    async def handle_jump(self, index: str) -> tuple[bool, str]:
        try:
            result = self._service().jump_novel(int(index), self.stream_id)
            text = (
                f"已跳转到第 {result['start']} 段并注入"
                f"（共 {result['total']} 段）"
            )
        except Exception as exc:
            text = f"跳转小说失败：{exc}"
        await self._reply(text)
        return True, text

    @cmd_route("reset")
    @cmd_route("重置")
    async def handle_reset(self) -> tuple[bool, str]:
        try:
            self._service().reset_novel(self.stream_id)
            text = "小说进度已重置到第一段。"
        except Exception as exc:
            text = f"重置小说进度失败：{exc}"
        await self._reply(text)
        return True, text

    @cmd_route("on")
    @cmd_route("启用")
    async def handle_on(self) -> tuple[bool, str]:
        try:
            self._service().save_novel_settings({"enabled": True})
            text = "小说自动注入已启用，下一轮主回复请求开始注入当前段落。"
        except Exception as exc:
            text = f"启用小说注入失败：{exc}"
        await self._reply(text)
        return True, text

    @cmd_route("off")
    @cmd_route("关闭")
    async def handle_off(self) -> tuple[bool, str]:
        try:
            self._service().save_novel_settings({"enabled": False})
            text = "小说自动注入已关闭。"
        except Exception as exc:
            text = f"关闭小说注入失败：{exc}"
        await self._reply(text)
        return True, text

    @cmd_route("file")
    @cmd_route("文件")
    async def handle_file(self, name: str) -> tuple[bool, str]:
        try:
            service = self._service()
            service.resolve_novel_file(name)
            service.save_novel_settings({"file": name})
            text = f"当前小说已切换为：{name}"
        except Exception as exc:
            text = f"切换小说失败：{exc}"
        await self._reply(text)
        return True, text

    @cmd_route("split")
    @cmd_route("分段")
    async def handle_split(self, mode: str) -> tuple[bool, str]:
        try:
            normalized = (mode or "").strip().lower()
            if normalized not in ("auto", "chapter", "char", "line"):
                raise ValueError("分段方式必须是 auto/chapter/char/line")
            self._service().save_novel_settings({"split_mode": normalized})
            state = self._service().novel_state(self.stream_id)
            text = (
                f"分段方式已设为 {normalized}，"
                f"当前切分：{state.get('label')}（{state.get('total')} 段）"
            )
        except Exception as exc:
            text = f"设置分段方式失败：{exc}"
        await self._reply(text)
        return True, text

    @cmd_route("batch")
    @cmd_route("批量")
    async def handle_batch(self, count: str) -> tuple[bool, str]:
        try:
            value = max(1, int(count))
            self._service().save_novel_settings({"batch_size": value})
            text = f"每次注入段数已设为 {value}。"
        except Exception as exc:
            text = f"设置每次注入段数失败：{exc}"
        await self._reply(text)
        return True, text

    @cmd_route("loop")
    async def handle_loop(self, value: str) -> tuple[bool, str]:
        try:
            enabled = _bool_text(value)
            self._service().save_novel_settings({"loop": enabled})
            text = f"循环模式已{'开启' if enabled else '关闭'}。"
        except Exception as exc:
            text = f"设置循环模式失败：{exc}"
        await self._reply(text)
        return True, text

    @cmd_route("var")
    @cmd_route("变量")
    async def handle_var(self, name: str) -> tuple[bool, str]:
        try:
            variable = (name or "").strip()
            if not variable:
                raise ValueError("变量名不能为空")
            self._service().save_novel_settings({"variable_name": variable})
            text = f"当前段落将写入变量 {{getvar::{variable}}}。"
        except Exception as exc:
            text = f"设置变量名失败：{exc}"
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

    async def execute(self, message_text: str) -> tuple[bool, str]:
        """显式桥接到 BaseCommand 的 Trie 路由执行。"""
        return await super().execute(message_text)

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
            elif normalized in (
                "replacement",
                "replace",
                "replacestring",
                "replace_string",
            ):
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
