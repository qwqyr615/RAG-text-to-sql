"""导入问题-SQL 示例到 Milvus。

运行：
    cd agent
    D:\\Anaconda\\envs\\sqllangchain\\python.exe -m rag.ingest_examples
"""

from core.config import settings
from rag.sql_example_store import ingest_sql_examples


def main() -> None:
    count = ingest_sql_examples(drop_old=True)
    print(f"RAG 示例导入完成：{count} 条")
    print(
        f"Milvus: {settings.milvus_uri} / "
        f"{settings.milvus_db_name} / {settings.milvus_collection_name}"
    )


if __name__ == "__main__":
    main()