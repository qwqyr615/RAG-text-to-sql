"""映射层：把「标准字段」对齐到客户实际列，并编译成运行时口径。

模块划分
--------
- :mod:`metadata.mapping.schema` —— ``mapping.yaml`` 的结构、加载与校验；
- :mod:`metadata.mapping.compiler` —— 编译成运行时视图（字段口径 / 指标 / 表语义）；
- :mod:`metadata.mapping.columns` —— SQL 改写器（标准字段口径 <-> 客户列口径）；
- :mod:`metadata.mapping.draft` —— 从库结构自动草拟映射（LLM + 规则兜底）；
- :mod:`metadata.mapping.cli` —— 接入与审核命令行。

默认映射从 ``settings.analysis_mapping`` 读取（见 ``core.config``）。它是**可选**的：
没有配置映射时系统退回「单表标准模型」——直接使用数据库里的列 ``COMMENT`` 作为
字段说明，也就是本项目原先的路线 B 行为。
"""

from __future__ import annotations

import logging
import os
from functools import lru_cache
from pathlib import Path

from core.config import BASE_DIR, settings
from metadata.mapping.compiler import CompiledMapping, ResolvedField, compile_profile
from metadata.mapping.schema import (
    MappingError,
    MappingIssue,
    MappingProfile,
    load_profile,
    mapping_coverage,
    validate_profile,
)

logger = logging.getLogger(__name__)

__all__ = [
    "CompiledMapping",
    "MappingError",
    "MappingIssue",
    "MappingProfile",
    "ResolvedField",
    "compile_profile",
    "default_mapping_path",
    "load_profile",
    "mapping_coverage",
    "resolve_mapping",
    "resolve_profile",
    "validate_profile",
]

#: 环境变量覆盖项，便于评测逐配置切换映射而不改 .env
MAPPING_ENV = "ANALYSIS_MAPPING"

#: 视为「不使用映射」的取值
_DISABLED = {"", "none", "null", "off", "false", "0"}


def default_mapping_path() -> Path | None:
    """当前生效的映射文件路径；不使用映射时返回 ``None``。"""
    raw = os.environ.get(MAPPING_ENV) or getattr(settings, "analysis_mapping", "") or ""
    value = str(raw).strip()
    if value.lower() in _DISABLED:
        return None
    path = Path(value)
    return path if path.is_absolute() else (BASE_DIR / path)


def resolve_profile(path: str | Path | None = None) -> MappingProfile | None:
    """加载映射；未配置或配置为 ``none`` 时返回 ``None``。"""
    target = Path(path) if path is not None else default_mapping_path()
    if target is None:
        return None
    return load_profile(target)


@lru_cache(maxsize=8)
def _cached_mapping(path_text: str) -> CompiledMapping:
    profile = load_profile(path_text)
    logger.info(
        "已加载字段映射：%s（表 %d 张）", path_text, len(profile.tables)
    )
    return compile_profile(profile)


def resolve_mapping(
    path: str | Path | None = None, *, use_cache: bool = True
) -> CompiledMapping | None:
    """取得已编译的映射；未配置时返回 ``None``（调用方退回标准模型）。

    参数:
        path: 显式指定映射文件；不传则用 ``ANALYSIS_MAPPING`` / ``settings.analysis_mapping``
        use_cache: 是否使用进程内缓存。CLI 审核期间应传 ``False``，
            否则改了 YAML 看不到效果。
    """
    target = Path(path) if path is not None else default_mapping_path()
    if target is None:
        return None
    if not target.is_absolute():
        target = BASE_DIR / target
    if not target.is_file():
        raise MappingError(f"映射文件不存在：{target}")

    if use_cache:
        return _cached_mapping(str(target))
    return compile_profile(load_profile(target))


def clear_mapping_cache() -> None:
    """清空映射缓存（测试与 CLI 审核用）。"""
    _cached_mapping.cache_clear()
