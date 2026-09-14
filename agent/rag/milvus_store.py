"""Milvus 向量库管理。

当前直接使用 pymilvus MilvusClient 封装，原因：
- 需要明确支持 MILVUS_DB_NAME
- 避免 langchain-milvus 在当前版本下 db_name/alias 兼容问题
- 保持 RAG 流程可控、易维护

嵌入模型仍然使用 LangChain 的 OpenAIEmbeddings 接入 SiliconFlow。
"""

import logging
from functools import lru_cache

from pymilvus import CollectionSchema, DataType, FieldSchema, MilvusClient

from core.config import settings
from rag.embeddings import get_embedding_model

logger = logging.getLogger(__name__)

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


def get_collection_dimension(
    client: MilvusClient,
    collection_name: str | None = None,
) -> int | None:
    """读取已有集合中向量字段的实际维度；读不到时返回 None。"""
    name = collection_name or settings.milvus_collection_name
    try:
        description = client.describe_collection(name)
    except Exception as exc:  # noqa: BLE001
        logger.warning("读取集合 %s 结构失败，跳过维度校验：%s", name, exc)
        return None

    for field in description.get("fields", []):
        if field.get("name") != VECTOR_FIELD:
            continue
        dim = (field.get("params") or {}).get("dim")
        return int(dim) if dim is not None else None
    return None


def ensure_collection(client: MilvusClient, drop_old: bool = False) -> bool:
    """确保集合存在，且向量维度与当前嵌入模型一致。

    返回 True 表示本次对集合做了重建（删除后按当前维度新建），
    调用方据此决定是否需要重新灌入数据。

    Milvus 的集合维度在建立后就固定了，如果更换了
    SILICONFLOW_EMBEDDING_MODEL（例如 1024 维换到 4096 维），
    旧集合不会自动适配，检索时会报
    "vector dimension mismatch, expected vector size(byte) 4096, actual 16384"。
    这里检测到维度不一致时自动删除旧集合并按新维度重建，避免持续报错。
    """
    collection_name = settings.milvus_collection_name
    dimension = get_embedding_dimension()
    rebuilt = False

    if client.has_collection(collection_name):
        existing_dimension = get_collection_dimension(client, collection_name)

        if existing_dimension is not None and existing_dimension != dimension:
            logger.warning(
                "集合 %s 向量维度为 %s，当前嵌入模型维度为 %s，"
                "自动删除旧集合并按新维度重建",
                collection_name,
                existing_dimension,
                dimension,
            )
            client.drop_collection(collection_name)
            rebuilt = True
        elif drop_old:
            client.drop_collection(collection_name)
            rebuilt = True

    if client.has_collection(collection_name):
        try:
            client.load_collection(collection_name)
        except Exception:
            pass
        return rebuilt

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
    return rebuilt