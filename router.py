"""tavern_preset_regex WebUI Router。"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from fastapi import HTTPException
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field
from src.app.plugin_system.api.log_api import get_logger
from src.core.components.base.router import BaseRouter

from .tavern_store import TavernDataService

logger = get_logger("tavern_preset_regex.webui")


class SetvarItemsPayload(BaseModel):
    items: list[dict[str, Any]] = Field(default_factory=list)


class RegexItemsPayload(BaseModel):
    items: list[dict[str, Any]] = Field(default_factory=list)


class ImportJsonPayload(BaseModel):
    content: str = Field(default="", description="JSON 文件内容")


class NovelConfigPayload(BaseModel):
    enabled: bool | None = Field(default=None, description="启用小说自动注入")
    file: str | None = Field(default=None, description="当前小说文件名")
    split_mode: str | None = Field(default=None, description="auto/chapter/char/line")
    char_size: int | None = Field(default=None, description="按字数分段的每段字数")
    lines_per_segment: int | None = Field(default=None, description="按行数分段的每段行数")
    chapter_pattern: str | None = Field(default=None, description="自定义章节正则")
    batch_size: int | None = Field(default=None, description="每次注入段数")
    loop: bool | None = Field(default=None, description="读完是否循环")
    variable_name: str | None = Field(default=None, description="当前段落写入的变量名")
    role: str | None = Field(default=None, description="注入条目角色")
    entry_enabled: bool | None = Field(default=None, description="是否注入小说条目")
    inject_when_empty: bool | None = Field(default=None, description="读完是否仍注入空条目")


class NovelJumpPayload(BaseModel):
    index: int = Field(default=1, description="跳转到的段号（1 起始）")
    stream_id: str = Field(default="", description="聊天流 ID，用于隔离进度")


def _novel_public_state(state: dict[str, Any]) -> dict[str, Any]:
    """裁掉完整正文，只保留 WebUI 需要的元信息与预览。"""
    segments: list[str] = list(state.get("segments") or [])
    index = int(state.get("index") or 0)
    preview = segments[index] if 0 <= index < len(segments) else ""
    return {
        "dir": state.get("dir", ""),
        "files": state.get("files", []),
        "active_file": state.get("active_file", ""),
        "config": state.get("config", {}),
        "total": state.get("total", 0),
        "index": index,
        "mode": state.get("mode", ""),
        "label": state.get("label", ""),
        "chars": state.get("chars", 0),
        "segment_titles": state.get("segment_titles", []),
        "segment_chars": state.get("segment_chars", []),
        "preview": preview[:2000],
        "error": state.get("error", ""),
    }


class TavernRegexAdminRouter(BaseRouter):
    """酒馆预设与正则编辑页。"""
    name: str = "tavern_preset_regex_webui"
    description: str = "tavern_preset_regex 预设与正则编辑后台"
    custom_route_path: str = "/plugins/tavern-preset-regex"
    cors_origins: list[str] = ["*"]

    def _service(self) -> TavernDataService:
        return TavernDataService(plugin=self.plugin)

    @staticmethod
    def _html() -> str:
        path = Path(__file__).with_name("webui.html")
        return path.read_text(encoding="utf-8")

    def register_endpoints(self) -> None:
        @self.app.get("/", response_class=HTMLResponse, include_in_schema=False)
        async def home() -> HTMLResponse:
            return HTMLResponse(self._html())

        @self.app.get("/api/state")
        async def get_state() -> dict[str, Any]:
            service = self._service()
            return {
                "setvar_prompts": service.list_setvar_items(),
                "preset_items": service.list_ordered_prompt_items(),
                "fixed_prompts": service.fixed_prompt_items(),
                "regexes": service.list_regex_items(),
                "novel": _novel_public_state(service.novel_state()),
                "paths": {
                    "tavern_dir": str(service.tavern_dir),
                    "setvar_path": str(service.setvar_path),
                    "regex_path": str(service.regex_path),
                    "novel_dir": str(service.novel_dir),
                },
            }

        @self.app.get("/api/novel")
        async def get_novel(stream_id: str = "") -> dict[str, Any]:
            state = self._service().novel_state(stream_id)
            return {"ok": True, **_novel_public_state(state)}

        @self.app.put("/api/novel")
        async def save_novel(payload: NovelConfigPayload) -> dict[str, Any]:
            service = self._service()
            updates = payload.model_dump(exclude_none=True)
            try:
                service.save_novel_settings(updates)
            except Exception as exc:
                logger.warning(f"保存小说设置失败: {exc}")
                raise HTTPException(status_code=400, detail=str(exc)) from exc
            return {"ok": True, **_novel_public_state(service.novel_state())}

        @self.app.post("/api/novel/next")
        async def novel_next(payload: NovelJumpPayload) -> dict[str, Any]:
            service = self._service()
            try:
                result = service.advance_novel(payload.stream_id)
            except Exception as exc:
                logger.warning(f"推进小说失败: {exc}")
                raise HTTPException(status_code=400, detail=str(exc)) from exc
            return {"ok": True, "result": result, **_novel_public_state(
                service.novel_state(payload.stream_id)
            )}

        @self.app.post("/api/novel/jump")
        async def novel_jump(payload: NovelJumpPayload) -> dict[str, Any]:
            service = self._service()
            try:
                result = service.jump_novel(payload.index, payload.stream_id)
            except Exception as exc:
                logger.warning(f"跳转小说失败: {exc}")
                raise HTTPException(status_code=400, detail=str(exc)) from exc
            return {"ok": True, "result": result, **_novel_public_state(
                service.novel_state(payload.stream_id)
            )}

        @self.app.post("/api/novel/reset")
        async def novel_reset(payload: NovelJumpPayload) -> dict[str, Any]:
            service = self._service()
            try:
                service.reset_novel(payload.stream_id)
            except Exception as exc:
                logger.warning(f"重置小说进度失败: {exc}")
                raise HTTPException(status_code=400, detail=str(exc)) from exc
            return {"ok": True, **_novel_public_state(service.novel_state(payload.stream_id))}

        @self.app.put("/api/setvar")
        async def save_setvar(payload: SetvarItemsPayload) -> dict[str, Any]:
            try:
                count = self._service().save_setvar_items(payload.items)
            except Exception as exc:
                logger.warning(f"保存 setvar 失败: {exc}")
                raise HTTPException(status_code=400, detail=str(exc)) from exc
            return {"ok": True, "count": count}

        @self.app.put("/api/regex")
        async def save_regex(payload: RegexItemsPayload) -> dict[str, Any]:
            try:
                count = self._service().save_regex_items(payload.items)
            except Exception as exc:
                logger.warning(f"保存 regex 失败: {exc}")
                raise HTTPException(status_code=400, detail=str(exc)) from exc
            return {"ok": True, "count": count}

        @self.app.post("/api/import")
        async def import_json(payload: ImportJsonPayload) -> dict[str, Any]:
            try:
                raw = json.loads(payload.content or "{}")
                result = self._service().detect_and_import_payload(raw)
            except Exception as exc:
                logger.warning(f"导入失败: {exc}")
                raise HTTPException(status_code=400, detail=str(exc)) from exc
            return {"ok": True, **result}


__all__ = ["TavernRegexAdminRouter"]
