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
                "paths": {
                    "tavern_dir": str(service.tavern_dir),
                    "setvar_path": str(service.setvar_path),
                    "regex_path": str(service.regex_path),
                },
            }

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
