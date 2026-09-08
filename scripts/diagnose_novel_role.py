"""检查小说条目角色的解析链路：TOML 配置 / novel/config.json / WebUI 显示。

用法：

    python scripts/diagnose_novel_role.py
    python scripts/diagnose_novel_role.py --data-dir D:\\...\\data\\tavern_preset_regex
"""

# ruff: noqa: I001 - 需要先补齐 sys.path 才能导入 neo-mofox 与插件模块

from __future__ import annotations

import argparse
import importlib
import io
import sys
import tempfile
from pathlib import Path

if hasattr(sys.stdout, "buffer"):
    sys.stdout = io.TextIOWrapper(
        sys.stdout.buffer, encoding="utf-8", errors="replace", line_buffering=True
    )

PLUGIN_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PLUGIN_ROOT.parent))

NEO_MOFOX = Path("D:/Neo-MoFox_Bots/myplugins/neo-mofox")
if NEO_MOFOX.is_dir():
    sys.path.insert(0, str(NEO_MOFOX))

config_module = importlib.import_module("tavern_preset_regex.config")
store_module = importlib.import_module("tavern_preset_regex.tavern_store")
novel_module = importlib.import_module("tavern_preset_regex.novel_store")

TavernRegexConfig = config_module.TavernRegexConfig
NovelSection = config_module.NovelSection
TavernDataService = store_module.TavernDataService
NovelRuntimeConfig = novel_module.NovelRuntimeConfig

NOVEL = "第一章 一\n内容一\n第二章 二\n内容二\n"


def make_service(root: Path, **novel_overrides) -> TavernDataService:
    config = TavernRegexConfig()
    config.plugin.data_dir = str(root)
    config.novel = NovelSection(**novel_overrides)
    plugin = type("_FakePlugin", (), {"config": config})()
    return TavernDataService(tavern_dir=root, plugin=plugin)


def show(service: TavernDataService, label: str) -> None:
    item = service.novel_prompt_item()
    settings = service.novel_settings()
    print(
        f"{label:<28} novel_settings.role={settings['role']!r:<12} "
        f"novel_prompt_item.role={item['role']!r}"
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--data-dir", default="")
    args = parser.parse_args()

    print("=== 1. 临时目录：TOML role 是否生效 ===")
    root = Path(tempfile.mkdtemp(prefix="novel_role_"))
    (root / "novel").mkdir()
    (root / "novel" / "demo.txt").write_text(NOVEL, encoding="utf-8")
    for role in ("system", "user", "assistant"):
        show(make_service(root, enabled=True, role=role), f"TOML role={role}")

    print("\n=== 2. 运行时 config.json 覆盖 TOML ===")
    service = make_service(root, enabled=True, role="system")
    show(service, "仅 TOML role=system")
    service.save_novel_settings({"role": "user"})
    show(service, "config.json role=user 后")
    print(f"  config.json 内容: {NovelRuntimeConfig(root / 'novel').load()}")
    # 之后再改 TOML，运行时文件仍会覆盖
    service2 = make_service(root, enabled=True, role="assistant")
    show(service2, "TOML 改成 assistant 后")
    print("  → 说明 config.json 优先于 TOML，改 TOML 不会生效")

    print("\n=== 3. 真实数据目录 ===")
    if args.data_dir:
        service3 = make_service(Path(args.data_dir), enabled=True)
        show(service3, Path(args.data_dir).name)
        runtime = NovelRuntimeConfig(Path(args.data_dir) / "novel")
        print(f"  config.json: {runtime.load() or '（不存在）'}")

    import shutil

    shutil.rmtree(root, ignore_errors=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
