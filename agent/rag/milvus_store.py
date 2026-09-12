"""Milvus 向量库管理。

当前直接使用 pymilvus MilvusClient 封装，原因：
- 需要明确支持 MILVUS_DB_NAME
- 避免 langchain-milvus 在当前版本下 db_name/alias 兼容问题
- 保持 RAG 流程可控、易维护

嵌入模型仍然使用 LangChain 的 OpenAIEmbeddings 接入 SiliconFlow。
"""

from functools import lru_cache

from pymilvus import CollectionSchema, DataType, FieldSchema, MilvusClient

from core.config import settings
from rag.embeddings import get_embedding_model

# 集合字段
PK_FIELD = "pk"
VECTOR_FIELD = "vector"
TEXT_FIELDS = ["question", "sql", "metrics", "tables", "description"]


def ensure_database() -> None:
    """确保 MILVUS_DB_NAME 对应的数据库存在。"""
    client = MilvusClient(uri=settings.milvus_uri)
    try:
        databases = client.list_databases()
        if settings.milvus_db_name not in databases:
            client.create_database(settings.milvus_db_name)
    finally:
        client.close()


def get_client() -> MilvusClient:
    """获取连接指定数据库的 MilvusClient。"""
    ensure_database()
    return MilvusClient(
        uri=settings.milvus_uri,
        db_name=settings.milvus_db_name,
    )


@lru_cache(maxsize=1)
def get_embedding_dimension() -> int:
    """通过一次测试嵌入获取向量维度。"""
    vector = get_embedding_model().embed_query("dimension_probe")
    return len(vector)


def ensure_collection(client: MilvusClient, drop_old: bool = False) -> None:
    """确保集合存在；drop_old=True 时先删除重建。"""
    collection_name = settings.milvus_collection_name

    if client.has_collection(collection_name) and drop_old:
        client.drop_collection(collection_name)

    if client.has_collection(collection_name):
        try:
            client.load_collection(collection_name)
        except Exception:
            pass
        return

    dimension = get_embedding_dimension()
    schema = CollectionSchema(
        fields=[
            FieldSchema(
                name=PK_FIELD,
                dtype=DataType.VARCHAR,
                is_primary=True,
                max_length=128,
            ),
            FieldSchema(
                name=VECTOR_FIELD,
                dtype=DataType.FLOAT_VECTOR,
                dim=dimension,
            ),
            FieldSchema(name="question", dtype=DataType.VARCHAR, max_length=4096),
            FieldSchema(name="sql", dtype=DataType.VARCHAR, max_length=65535),
            FieldSchema(name="metrics", dtype=DataType.VARCHAR, max_length=1024),
            FieldSchema(name="tables", dtype=DataType.VARCHAR, max_length=1024),
            FieldSchema(name="description", dtype=DataType.VARCHAR, max_length=1024),
        ],
        enable_dynamic_field=False,
    )

    index_params = client.prepare_index_params()
    index_params.add_index(
        field_name=VECTOR_FIELD,
        index_type="AUTOINDEX",
        metric_type="COSINE",
    )

    client.create_collection(
        collection_name=collection_name,
        schema=schema,
        index_params=index_params,
    )