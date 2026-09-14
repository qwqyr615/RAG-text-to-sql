"""HTTP 适配层（FastAPI）。

本模块把 ``agent/`` 下已有的 Agent 内核（``EnterpriseAgent`` /
``metadata_service`` / ``knowledge_service`` / ``tools.modeling``）包装成
REST + SSE 接口，供 Java 网关（Spring Boot）调用。

设计原则：
- **不改内核**：所有能力都复用已有函数，本层只做「参数校验 + JSON 序列化 +
  错误契约统一 + 异步任务编排」。
- **必须在 agent 目录下启动**：内核各模块使用扁平导入（如
  ``from agents.text2sql_agent import ...``），因此 ``agent/`` 必须在
  ``sys.path`` 中。启动方式见 ``server/main.py`` 顶部说明。
"""
