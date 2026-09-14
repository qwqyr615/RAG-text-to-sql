"""初始化预置数据底座（单表宽表模型，路线 B）。

数据分层
--------
- **原始层** ``intelligent_production_iiot``：CSV 导入的原样数据，本套脚本只读不写。
- **服务层** ``fact_production_record``：显式 DDL 治理后的宽表，Agent 只查这一张。

不再派生 ``dim_*`` 维表，原因见 ``docs/DATA_MODEL.md``：维表原先由同一张宽表
``SELECT DISTINCT`` 派生，属性列从未填充，而 fact 表已冗余保存全部维度编码，
实测 JOIN 结果与直接读 fact 完全相同，只会让 LLM 生成无收益的关联查询。

DDL 与装载 SQL 放在 ``sql/`` 目录（可 review、可 diff、可单独跑），本脚本只负责按顺序执行：

1. ``01_drop_legacy_dim_tables.sql``     删除历史派生维表
2. ``02_create_fact_production_record.sql`` 显式 DDL：类型 / 主键 / NOT NULL / COMMENT / 索引
3. ``03_load_fact_production_record.sql``   幂等装载：TRUNCATE + 显式列清单 + CAST

脚本可重复执行（先删后建 + TRUNCATE 重灌，源表不动）。

运行::

    cd agent
    D:\\Anaconda\\envs\\sqllangchain\\python.exe scripts\\init_preset_schema.py

之后建议跑一次健康检查::

    D:\\Anaconda\\envs\\sqllangchain\\python.exe scripts\\check_schema_health.py
"""

from __future__ import annotations

import sys
from pathlib import Path

# 允许用 `python scripts/xxx.py` 直接运行。
# 这种方式下 sys.path[0] 是 scripts/ 而不是 agent/，需要手动补上项目根目录，
# 否则 `import core/metadata/knowledge/...` 会报 ModuleNotFoundError
# （此前只能靠 PyCharm 自动把 content root 加进 PYTHONPATH 才跑得通）。
_AGENT_ROOT = Path(__file__).resolve().parents[1]
if str(_AGENT_ROOT) not in sys.path:
    sys.path.insert(0, str(_AGENT_ROOT))

from sqlalchemy import text  # noqa: E402
from sqlalchemy.engine import Engine  # noqa: E402

from knowledge.knowledge_service import export_knowledge_json  # noqa: E402
from metadata.metadata_service import export_metadata_json  # noqa: E402
from metadata.schema_ddl import (  # noqa: E402
    DDL_FILE,
    LOAD_FILE,
    SCHEMA_FILES,
    load_statements,
    resolve_path,
)
from tools.database import get_engine  # noqa: E402


def apply_schema(engine: Engine, *, verbose: bool = True) -> int:
    """按顺序执行 ``sql/`` 下的所有语句，返回执行的语句条数。"""
    executed = 0
    with engine.begin() as conn:
        for file_name in SCHEMA_FILES:
            path = resolve_path(file_name)
            for statement in load_statements(path):
                conn.execute(text(statement))
                executed += 1
                if verbose:
                    preview = " ".join(statement.split())[:72]
                    print(f"  [{file_name}] {preview} …")
    return executed


def export_json() -> tuple[Path, Path]:
    """刷新 outputs/ 下的 metadata 与 knowledge JSON。"""
    return export_metadata_json(), export_knowledge_json()


def main() -> None:
    engine = get_engine()
    print("=" * 70)
    print("初始化预置数据底座（单表宽表模型）")
    print("=" * 70)
    print(f"数据源：{engine.url.render_as_string(hide_password=True)}")
    print(f"DDL ：{resolve_path(DDL_FILE)}")
    print(f"装载：{resolve_path(LOAD_FILE)}")
    print("-" * 70)

    executed = apply_schema(engine)
    print("-" * 70)
    print(f"已执行 {executed} 条语句。")

    with engine.connect() as conn:
        service_rows = conn.execute(
            text("SELECT COUNT(*) FROM fact_production_record")
        ).scalar_one()
    print(f"服务层 fact_production_record：{service_rows} 行")

    metadata_path, knowledge_path = export_json()
    print(f"metadata JSON：{metadata_path}")
    print(f"knowledge JSON：{knowledge_path}")
    print("-" * 70)
    print("下一步：D:\\Anaconda\\envs\\sqllangchain\\python.exe scripts\\check_schema_health.py")


if __name__ == "__main__":
    main()
