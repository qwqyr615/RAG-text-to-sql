"""初始化多表预置数据底座。

从单张宽表 intelligent_production_iiot 构建简易多表模型：
- dim_line：生产线维度
- dim_machine：设备维度
- dim_product：产品维度
- dim_batch：批次维度
- fact_production_record：生产记录事实表

脚本可重复执行（使用 CREATE TABLE IF NOT EXISTS / INSERT IGNORE）。
运行：
    cd agent
    D:\\Anaconda\\envs\\sqllangchain\\python.exe scripts\\init_preset_schema.py
"""

from sqlalchemy import text

from tools.database import get_engine

SOURCE_TABLE = "intelligent_production_iiot"

SCHEMA_STATEMENTS = [
    """
    CREATE TABLE IF NOT EXISTS dim_line (
        line_id VARCHAR(100) PRIMARY KEY,
        description VARCHAR(255)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS dim_machine (
        machine_id VARCHAR(100) PRIMARY KEY,
        line_id VARCHAR(100)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS dim_product (
        product_type VARCHAR(100) PRIMARY KEY,
        product_name VARCHAR(255)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS dim_batch (
        batch_id VARCHAR(100) PRIMARY KEY
    )
    """,
    f"""
    CREATE TABLE IF NOT EXISTS fact_production_record LIKE {SOURCE_TABLE}
    """,
]


def main() -> None:
    engine = get_engine()
    with engine.begin() as conn:
        for statement in SCHEMA_STATEMENTS:
            conn.execute(text(statement))

        conn.execute(
            text(
                f"""
                INSERT IGNORE INTO dim_line (line_id)
                SELECT DISTINCT production_line
                FROM {SOURCE_TABLE}
                WHERE production_line IS NOT NULL
                """
            )
        )
        conn.execute(
            text(
                f"""
                INSERT IGNORE INTO dim_machine (machine_id, line_id)
                SELECT DISTINCT machine_id, production_line
                FROM {SOURCE_TABLE}
                WHERE machine_id IS NOT NULL
                """
            )
        )
        conn.execute(
            text(
                f"""
                INSERT IGNORE INTO dim_product (product_type)
                SELECT DISTINCT product_type
                FROM {SOURCE_TABLE}
                WHERE product_type IS NOT NULL
                """
            )
        )
        conn.execute(
            text(
                f"""
                INSERT IGNORE INTO dim_batch (batch_id)
                SELECT DISTINCT batch_id
                FROM {SOURCE_TABLE}
                WHERE batch_id IS NOT NULL
                """
            )
        )
        conn.execute(
            text(
                f"""
                INSERT IGNORE INTO fact_production_record
                SELECT * FROM {SOURCE_TABLE}
                """
            )
        )

    print("多表预置底座初始化完成。")
    print("tables: dim_line, dim_machine, dim_product, dim_batch, fact_production_record")


if __name__ == "__main__":
    main()
