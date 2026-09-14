# RAG 模块设计

目标：在 Text-to-SQL 生成 SQL 前，检索相似问题-SQL 示例并注入 Prompt。

目录：
- rag/embeddings.py：SiliconFlow 嵌入模型
- rag/milvus_store.py：Milvus 连接、数据库和集合管理
- rag/sql_example_store.py：示例导入与检索
- rag/retriever.py：检索结果格式化
- rag/ingest_examples.py：导入示例脚本
- rag/examples/sql_examples.json：示例问题与 SQL

配置：SILICONFLOW_API_KEY / SILICONFLOW_BASE_URL / SILICONFLOW_EMBEDDING_MODEL / MILVUS_URI / MILVUS_DB_NAME / MILVUS_COLLECTION_NAME / RAG_TOP_K

使用：
1. D:\Anaconda\envs\sqllangchain\python.exe -m rag.ingest_examples
2. D:\Anaconda\envs\sqllangchain\python.exe main.py

说明：当前直接使用 pymilvus.MilvusClient，避免 langchain-milvus 在 db_name 下的连接兼容问题。

向量维度：集合维度由 `get_embedding_dimension()` 按当前 `SILICONFLOW_EMBEDDING_MODEL` 探测得到（Qwen/Qwen3-VL-Embedding-8B 为 4096 维，bge-m3 为 1024 维）。
`ensure_collection()` 会在每次写入/检索前比对集合实际维度与当前模型维度，一旦不一致（换模型后旧集合不会自动适配，检索会报
`vector dimension mismatch, expected vector size(byte) 4096, actual 16384`），自动删除旧集合并按新维度重建；
检索路径（`search_sql_examples`）检测到重建后会用 `rag/examples/sql_examples.json` 立即回灌数据，无需人工干预。
如需完全手动重建：`D:\Anaconda\envs\sqllangchain\python.exe -m rag.ingest_examples`（默认 `drop_old=True`）。
