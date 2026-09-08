"""``data/tavern_preset_regex/novel`` 下小说的导入、分段与进度管理。

设计要点：

- 小说文件放在 ``novel/`` 目录，支持 ``.txt`` / ``.md``，编码自动嗅探
  （UTF-8 → GB18030）。
- 分段支持四种模式：``auto``（自动识别章节标题，识别不到退化为字数）、
  ``chapter``、``char``、``line``。
- 进度按 ``stream_id`` 隔离，存在 ``novel/progress.json``，重启不丢；
  找不到 stream 时回落到 ``default``。
- 本模块不依赖 MoFox 运行时，方便单测。
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Literal

from src.app.plugin_system.api.log_api import get_logger

logger = get_logger("tavern_preset_regex.novel")

# 「📖小说当前段落」这个虚拟条目在统一顺序表里的 identifier。
NOVEL_ENTRY_ID = "novel_current"
NOVEL_ENTRY_NAME = "📖小说当前段落"
# 条目内容用宏写法，渲染时由当前段落填充，这样变量名可以自由配置。
NOVEL_ENTRY_CONTENT = "{{getvar::current_chapter}}"

# 「本轮新输入」虚拟条目：把对话块里的最后一条 user 单独拆出来，允许拖顺序。
NEW_INPUT_ENTRY_ID = "mofox_new_input"
NEW_INPUT_ENTRY_NAME = "🆕本轮新输入"

NOVEL_SUFFIXES = (".txt", ".md")
DEFAULT_STREAM_KEY = "default"
MAX_TEXT_BYTES = 64 * 1024 * 1024

# novel/ 目录里的说明文件不算小说正文
_SKIP_FILE_STEMS = frozenset(("readme", "readme.cn", "说明", "index"))

SplitMode = Literal["auto", "chapter", "char", "line"]

# —— 中文小说章节标题识别 ——
_CN_NUM_CHARS = "零〇一二三四五六七八九十百千万两"
_HEADING_DI_RE = re.compile(
    rf"^第[0-9０-９{_CN_NUM_CHARS}]+[章节回卷部篇集幕話话](?:\s|[:：.、．]|$)"
)
_HEADING_SPECIAL_RE = re.compile(
    r"^(序章|序言|楔子|引子|前言|自序|终章|尾声|后记|番外(?:篇)?|外传|卷首语)"
    r"(?:\s|[:：.、．]|$)"
)
_HEADING_NUM_RE = re.compile(r"^\s*[0-9０-９]+\s*[.、．·:：)）]")
_HEADING_CN_NUM_RE = re.compile(
    r"^\s*[（(]?[一二三四五六七八九十百千万零〇]+\s*[、.．)）]"
)
_HEADING_DI_SPECIAL_RE = re.compile(
    rf"^(?:第[0-9０-９{_CN_NUM_CHARS}]+[章节回卷部篇集幕話话]"
    r"|序章|序言|楔子|引子|前言|自序|终章|尾声|后记|番外(?:篇)?|外传|卷首语)"
    r"(?:\s|[:：.、．]|$)"
)

_SENTENCE_END_RE = re.compile(r"[。！？!?；;]")


def _read_text_auto_encoding(path: Path) -> str:
    """按 UTF-8 → GB18030 → 宽松 UTF-8 的顺序解码小说文件。"""
    raw = path.read_bytes()[:MAX_TEXT_BYTES]
    for encoding in ("utf-8-sig", "utf-8"):
        try:
            return raw.decode(encoding)
        except UnicodeDecodeError:
            continue
    for encoding in ("gb18030", "big5"):
        try:
            return raw.decode(encoding)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", errors="replace")


def detect_chapter_format(text: str) -> dict[str, Any]:
    """扫描全文，判断章节标题风格。

    返回 ``{"mode", "label", "count", "pattern"}``；``mode`` 为 ``chapter``
    表示识别到章节标题，``char`` 表示没识别到（调用方应退化为字数切分）。
    """
    clean = (text or "").replace("\r\n", "\n").strip()
    empty = {"mode": "char", "label": "", "count": 0, "pattern": None}
    if not clean:
        return empty

    counters = {"di": 0, "special": 0, "num": 0, "cnnum": 0}
    for raw_line in clean.split("\n"):
        line = raw_line.strip()
        if not line:
            continue
        if _HEADING_DI_RE.match(line):
            counters["di"] += 1
        elif _HEADING_SPECIAL_RE.match(line):
            counters["special"] += 1
        elif _HEADING_NUM_RE.match(line):
            counters["num"] += 1
        elif _HEADING_CN_NUM_RE.match(line):
            counters["cnnum"] += 1

    if counters["di"] + counters["special"] >= 2:
        return {
            "mode": "chapter",
            "label": "第X章/序章等标题",
            "count": counters["di"] + counters["special"],
            "pattern": _HEADING_DI_SPECIAL_RE,
        }
    if counters["num"] >= 3:
        return {
            "mode": "chapter",
            "label": "数字序号",
            "count": counters["num"],
            "pattern": _HEADING_NUM_RE,
        }
    if counters["cnnum"] >= 3:
        return {
            "mode": "chapter",
            "label": "中文序号",
            "count": counters["cnnum"],
            "pattern": _HEADING_CN_NUM_RE,
        }
    return empty


def _split_by_chapter(text: str, pattern: re.Pattern[str]) -> list[str]:
    """按标题行切分，标题行保留在段首。"""
    clean = (text or "").replace("\r\n", "\n").strip()
    if not clean:
        return []
    parts: list[list[str]] = []
    current: list[str] = []
    for line in clean.split("\n"):
        if pattern.match(line.strip()):
            if "\n".join(current).strip():
                parts.append(current)
            current = [line]
        else:
            current.append(line)
    if "\n".join(current).strip():
        parts.append(current)
    return [
        part.strip() for part in ("\n".join(item) for item in parts) if part.strip()
    ]


def _split_by_char(text: str, size: int) -> list[str]:
    """按目标字数切，断点优先换行、其次句末标点，避免拦腰斩句。"""
    clean = (text or "").replace("\r\n", "\n").strip()
    if not clean:
        return []
    size = max(1, int(size))
    if len(clean) <= size:
        return [clean]

    chunks: list[str] = []
    start = 0
    total = len(clean)
    while start < total:
        end = min(start + size, total)
        if end < total:
            window_start = start + int(size * 0.5)
            window = clean[window_start:end]
            best = -1
            newline = window.rfind("\n")
            if newline >= 0:
                best = window_start + newline + 1
            for match in _SENTENCE_END_RE.finditer(window):
                best = window_start + match.end()
            if start < best < end:
                end = best
        chunk = clean[start:end].strip()
        if chunk:
            chunks.append(chunk)
        start = end
    return chunks


def _split_by_line(text: str, lines_per_segment: int) -> list[str]:
    """按固定行数切，空行不会单独成段。"""
    clean = (text or "").replace("\r\n", "\n").strip()
    if not clean:
        return []
    size = max(1, int(lines_per_segment))
    all_lines = clean.split("\n")
    chunks: list[str] = []
    for start in range(0, len(all_lines), size):
        chunk = "\n".join(all_lines[start : start + size]).strip()
        if chunk:
            chunks.append(chunk)
    return chunks


def split_novel(
    text: str,
    *,
    mode: str = "auto",
    char_size: int = 10000,
    lines_per_segment: int = 60,
    chapter_pattern: str = "",
) -> dict[str, Any]:
    """按指定模式切分小说，返回段落列表与识别信息。"""
    clean = (text or "").replace("\r\n", "\n").strip()
    if not clean:
        return {
            "segments": [],
            "mode": "char",
            "label": "",
            "detected": {"mode": "char", "label": "", "count": 0},
        }

    detected = detect_chapter_format(clean)
    pattern = detected["pattern"]
    if chapter_pattern.strip():
        try:
            pattern = re.compile(chapter_pattern.strip())
        except re.error as exc:
            logger.warning(f"自定义章节正则无法编译，忽略: {exc}")
            pattern = detected["pattern"]

    requested = (mode or "auto").strip().lower()
    if requested not in ("auto", "chapter", "char", "line"):
        requested = "auto"

    if requested == "chapter" or (
        requested == "auto" and detected["mode"] == "chapter"
    ):
        if pattern is None:
            logger.info("未识别到章节标题，退化为按字数切分")
            return {
                "segments": _split_by_char(clean, char_size),
                "mode": "char",
                "label": "未识别到章节标题（按字数）",
                "detected": detected,
            }
        segments = _split_by_chapter(clean, pattern)
        return {
            "segments": segments,
            "mode": "chapter",
            "label": detected["label"] or "自定义章节正则",
            "detected": detected,
        }

    if requested == "line":
        return {
            "segments": _split_by_line(clean, lines_per_segment),
            "mode": "line",
            "label": f"每 {max(1, int(lines_per_segment))} 行",
            "detected": detected,
        }

    return {
        "segments": _split_by_char(clean, char_size),
        "mode": "char",
        "label": f"每 {max(1, int(char_size))} 字",
        "detected": detected,
    }


def segment_title(segment: str) -> str:
    """取段落标题：首行是标题行就用首行，否则用开头若干字。"""
    if not segment:
        return ""
    first_line = next(
        (line.strip() for line in segment.split("\n") if line.strip()), ""
    )
    candidate = first_line or segment.strip()
    if (
        _HEADING_DI_RE.match(candidate)
        or _HEADING_SPECIAL_RE.match(candidate)
        or _HEADING_NUM_RE.match(candidate)
        or _HEADING_CN_NUM_RE.match(candidate)
    ):
        return candidate if len(candidate) <= 28 else candidate[:28] + "…"
    flat = re.sub(r"\s+", " ", candidate)
    return flat if len(flat) <= 20 else flat[:20] + "…"


def _read_json(path: Path, default: Any) -> Any:
    if not path.is_file():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8-sig"))
    except (json.JSONDecodeError, OSError) as exc:
        logger.warning(f"读取 {path.name} 失败，按空数据处理: {exc}")
        return default


def _write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    temp.replace(path)


# 小说运行时设置项：可在 WebUI 里改，存 novel/config.json；
# 插件 config.toml 里的 [novel] 提供默认值，运行时可改项优先。
NOVEL_RUNTIME_KEYS: tuple[str, ...] = (
    "enabled",
    "file",
    "split_mode",
    "char_size",
    "lines_per_segment",
    "chapter_pattern",
    "batch_size",
    "loop",
    "variable_name",
    "role",
    "entry_enabled",
    "inject_when_empty",
)

# 预设顺序相关的运行时设置（同样存在 novel/config.json，由 WebUI 保存）
BLOCK_RUNTIME_KEYS: tuple[str, ...] = (
    "user_block_position",
    "head_preset_text",
    "head_preset_role",
)

ALL_RUNTIME_KEYS: tuple[str, ...] = NOVEL_RUNTIME_KEYS + BLOCK_RUNTIME_KEYS

_BLOCK_DEFAULTS: dict[str, Any] = {
    "user_block_position": "auto",
    "head_preset_text": "",
    "head_preset_role": "user",
}

_NOVEL_DEFAULTS: dict[str, Any] = {
    "enabled": False,
    "file": "",
    "split_mode": "auto",
    "char_size": 10000,
    "lines_per_segment": 60,
    "chapter_pattern": "",
    "batch_size": 1,
    "loop": False,
    "variable_name": "current_chapter",
    "role": "system",
    "entry_enabled": True,
    "inject_when_empty": False,
}

_NOVEL_INT_KEYS = {"char_size", "lines_per_segment", "batch_size"}
_NOVEL_BOOL_KEYS = {"enabled", "loop", "entry_enabled", "inject_when_empty"}


def normalize_novel_config(raw: dict[str, Any] | None) -> dict[str, Any]:
    """把任意来源的配置规范化成完整的小说设置字典。"""
    merged = dict(_NOVEL_DEFAULTS)
    merged.update(_BLOCK_DEFAULTS)
    for key, value in (raw or {}).items():
        if key not in ALL_RUNTIME_KEYS or value is None:
            continue
        merged[key] = value

    for key in _NOVEL_INT_KEYS:
        try:
            merged[key] = max(1, int(merged[key]))
        except (TypeError, ValueError):
            merged[key] = _NOVEL_DEFAULTS[key]
    for key in _NOVEL_BOOL_KEYS:
        merged[key] = bool(merged[key])
    merged["split_mode"] = (
        str(merged["split_mode"])
        if str(merged["split_mode"]) in ("auto", "chapter", "char", "line")
        else "auto"
    )
    merged["role"] = (
        str(merged["role"])
        if str(merged["role"]) in ("system", "user", "assistant")
        else "system"
    )
    merged["variable_name"] = str(merged["variable_name"] or "current_chapter").strip()
    merged["file"] = str(merged["file"] or "").strip()
    merged["chapter_pattern"] = str(merged["chapter_pattern"] or "")
    merged["user_block_position"] = (
        str(merged["user_block_position"])
        if str(merged["user_block_position"])
        in ("auto", "strict", "before_last_user", "head_tail", "after_system", "end")
        else "auto"
    )
    merged["head_preset_role"] = (
        str(merged["head_preset_role"])
        if str(merged["head_preset_role"]) in ("user", "system", "assistant")
        else "user"
    )
    merged["head_preset_text"] = str(merged["head_preset_text"] or "")
    return merged


class NovelRuntimeConfig:
    """``novel/config.json``：只保存「在 WebUI 里显式改过」的设置。

    这里刻意不写入默认值：没有列进本文件的键继续使用插件 ``config.toml``，
    这样手改 TOML 对没被覆盖的键依然生效。
    """

    def __init__(self, novel_dir: Path) -> None:
        self.path = Path(novel_dir) / "config.json"

    def load(self) -> dict[str, Any]:
        payload = _read_json(self.path, {})
        if not isinstance(payload, dict):
            return {}
        return {
            key: value
            for key, value in payload.items()
            if key in ALL_RUNTIME_KEYS and value is not None
        }

    def save(self, updates: dict[str, Any]) -> dict[str, Any]:
        """合并写入覆盖项，并返回写入后的覆盖项（未做默认值补全）。"""
        current = self.load()
        for key, value in (updates or {}).items():
            if key in ALL_RUNTIME_KEYS and value is not None:
                current[key] = value
        self.path.parent.mkdir(parents=True, exist_ok=True)
        _write_json(self.path, current)
        return current


class NovelService:
    """小说文件的扫描、分段与进度读写。"""

    def __init__(self, novel_dir: Path) -> None:
        self.novel_dir = Path(novel_dir)
        self.progress_path = self.novel_dir / "progress.json"
        self._cache: dict[str, dict[str, Any]] = {}

    # ----- 目录与文件 -----
    def ensure_dir(self) -> None:
        self.novel_dir.mkdir(parents=True, exist_ok=True)

    def list_files(self) -> list[dict[str, Any]]:
        """列出 ``novel/`` 下的小说文件。

        自动跳过目录说明文件（``README.md`` 等），避免它被当成小说正文。
        """
        self.ensure_dir()
        items: list[dict[str, Any]] = []
        for path in sorted(self.novel_dir.iterdir()):
            if not path.is_file() or path.suffix.lower() not in NOVEL_SUFFIXES:
                continue
            if path.stem.lower() in _SKIP_FILE_STEMS:
                continue
            stat = path.stat()
            items.append(
                {
                    "name": path.name,
                    "size": stat.st_size,
                    "mtime": stat.st_mtime,
                }
            )
        return items

    def resolve_file(self, name: str) -> Path:
        """把小说文件名解析成路径，拒绝目录穿越。"""
        candidate = (name or "").strip()
        if not candidate:
            raise ValueError("小说文件名为空")
        path = (self.novel_dir / Path(candidate).name).resolve()
        if self.novel_dir.resolve() not in path.parents:
            raise ValueError(f"小说文件必须位于 {self.novel_dir} 下")
        if not path.is_file():
            raise ValueError(f"小说文件不存在: {candidate}")
        if path.suffix.lower() not in NOVEL_SUFFIXES:
            raise ValueError(f"只支持 {'/'.join(NOVEL_SUFFIXES)} 文件: {candidate}")
        return path

    # ----- 分段 -----
    def load_segments(
        self,
        name: str,
        *,
        mode: str = "auto",
        char_size: int = 10000,
        lines_per_segment: int = 60,
        chapter_pattern: str = "",
        use_cache: bool = True,
    ) -> dict[str, Any]:
        """读取并切分小说，带 mtime 缓存。"""
        path = self.resolve_file(name)
        mtime = path.stat().st_mtime
        cache_key = f"{path}|{mode}|{char_size}|{lines_per_segment}|{chapter_pattern}"
        cached = self._cache.get(cache_key)
        if use_cache and cached is not None and cached.get("mtime") == mtime:
            return cached

        text = _read_text_auto_encoding(path)
        result = split_novel(
            text,
            mode=mode,
            char_size=char_size,
            lines_per_segment=lines_per_segment,
            chapter_pattern=chapter_pattern,
        )
        payload = {
            "mtime": mtime,
            "name": path.name,
            "chars": len(text),
            "segments": result["segments"],
            "mode": result["mode"],
            "label": result["label"],
            "detected": result["detected"],
        }
        self._cache[cache_key] = payload
        return payload

    # ----- 进度 -----
    def load_progress(self) -> dict[str, Any]:
        payload = _read_json(self.progress_path, {})
        if not isinstance(payload, dict):
            return {}
        novels = payload.get("novels")
        if not isinstance(novels, dict):
            payload = {"novels": {}}
        return payload

    def save_progress(self, payload: dict[str, Any]) -> None:
        self.ensure_dir()
        _write_json(self.progress_path, payload)

    @staticmethod
    def _stream_key(stream_id: str | None) -> str:
        key = str(stream_id or "").strip()
        return key or DEFAULT_STREAM_KEY

    def get_index(self, novel: str, stream_id: str | None = None) -> int:
        """读取某本小说在某个聊天流里的进度。"""
        payload = self.load_progress()
        entry = payload.get("novels", {}).get(novel, {})
        if not isinstance(entry, dict):
            return 0
        streams = entry.get("streams", {})
        if not isinstance(streams, dict):
            return 0
        value = streams.get(self._stream_key(stream_id), 0)
        try:
            return max(0, int(value))
        except (TypeError, ValueError):
            return 0

    def set_index(
        self,
        novel: str,
        index: int,
        stream_id: str | None = None,
    ) -> int:
        """写入某本小说在某个聊天流里的进度。"""
        payload = self.load_progress()
        novels = payload.setdefault("novels", {})
        entry = novels.setdefault(novel, {})
        if not isinstance(entry, dict):
            entry = {}
            novels[novel] = entry
        streams = entry.setdefault("streams", {})
        if not isinstance(streams, dict):
            streams = {}
            entry["streams"] = streams
        streams[self._stream_key(stream_id)] = max(0, int(index))
        self.save_progress(payload)
        return max(0, int(index))

    def reset_progress(self, novel: str, stream_id: str | None = None) -> None:
        self.set_index(novel, 0, stream_id)

    # ----- 推进 -----
    def advance(
        self,
        novel: str,
        segments: list[str],
        *,
        batch_size: int = 1,
        loop: bool = False,
        stream_id: str | None = None,
    ) -> dict[str, Any]:
        """取当前段落（可批量拼接）并推进指针。

        返回 ``{"content", "start", "end", "index", "total", "finished", "looped"}``，
        其中 ``start``/``end`` 为 1 起始的段号区间；``finished`` 表示本次已读到末尾。
        """
        total = len(segments)
        if total == 0:
            return {
                "content": "",
                "start": 0,
                "end": 0,
                "index": 0,
                "total": 0,
                "finished": True,
                "looped": False,
            }

        size = max(1, int(batch_size or 1))
        index = min(self.get_index(novel, stream_id), total)
        looped = False
        if index >= total:
            if not loop:
                return {
                    "content": "",
                    "start": 0,
                    "end": 0,
                    "index": index,
                    "total": total,
                    "finished": True,
                    "looped": False,
                }
            index = 0
            looped = True

        end = min(index + size, total)
        content = "\n\n".join(segments[index:end])
        self.set_index(novel, end, stream_id)
        return {
            "content": content,
            "start": index + 1,
            "end": end,
            "index": end,
            "total": total,
            "finished": end >= total,
            "looped": looped,
        }

    def peek(
        self,
        novel: str,
        segments: list[str],
        *,
        batch_size: int = 1,
        loop: bool = False,
        stream_id: str | None = None,
    ) -> dict[str, Any]:
        """只看当前段落内容，不推进指针。"""
        total = len(segments)
        if total == 0:
            return {"content": "", "start": 0, "end": 0, "total": 0, "finished": True}
        size = max(1, int(batch_size or 1))
        index = min(self.get_index(novel, stream_id), total)
        if index >= total:
            if not loop:
                return {
                    "content": "",
                    "start": 0,
                    "end": 0,
                    "total": total,
                    "finished": True,
                }
            index = 0
        end = min(index + size, total)
        return {
            "content": "\n\n".join(segments[index:end]),
            "start": index + 1,
            "end": end,
            "total": total,
            "finished": end >= total,
        }
