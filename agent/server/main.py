"""FastAPI 服务入口。

启动方式（**必须让 ``agent/`` 在 sys.path 上**，因为内核各模块使用扁平导入）：

    cd agent
    python -m uvicorn server.main:app --host 0.0.0.0 --port 8000 --reload

或直接运行本文件::

    cd agent
    python server/main.py

启动后：
- Swagger 文档：http://127.0.0.1:8000/docs
- 健康检查：http://127.0.0.1:8000/api/v1/system/health

架构位置::

    React (FRONTEND/)
        ↓ HTTP / SSE
    Spring Boot 网关 (backend/)
        ↓ HTTP / SSE
    FastAPI 适配层（本模块）
        ↓ 函数直接调用
    Agent 内核 (agents/ metadata/ knowledge/ tools/)
        ↓
    MySQL + Milvus + DeepSeek
"""

from __future__ import annotations

import logging
import os
import sys
from pathlib import Path

# ---------------------------------------------------------------------------
# 路径引导：必须在导入 server.* 之前执行
# ---------------------------------------------------------------------------
# 目的：无论用 `python -m uvicorn server.main:app` 还是 `python server/main.py`
# 启动，都保证 `/agent` 目录位于 sys.path[0]，使内核对齐的扁平导入
# (`from agents.text2sql_agent import ...`) 能够解析。
_AGENT_DIR = Path(__file__).resolve().parent.parent
if str(_AGENT_DIR) not in sys.path:
    sys.path.insert(0, str(_AGENT_DIR))

from fastapi import FastAPI, Request  # noqa: E402
from fastapi.middleware.cors import CORSMiddleware  # noqa: E402
from fastapi.responses import JSONResponse  # noqa: E402

from server.routes import (  # noqa: E402
    agent_router,
    knowledge_router,
    metadata_router,
    modeling_router,
    system_router,
)
from server.schemas import fail  # noqa: E402

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-7s | %(name)s | %(message)s",
)
logger = logging.getLogger("server.main")


def create_app() -> FastAPI:
    """构造 FastAPI 应用。"""
    app = FastAPI(
        title="企业数据底座智能问析 Agent API",
        description=(
            "面向企业数据底座的智能问析 Agent 服务。\n\n"
            "统一返回信封 `{code, msg, data}`，与 Java 网关 "
            "`com.sky.result.Result` 字段级一致。"
        ),
        version="1.0.0",
        docs_url="/docs",
        redoc_url="/redoc",
    )

    # CORS：前端开发服务器（Vite 默认 5173）与 Java 网关都需要直连
    origins = os.getenv(
        "CORS_ALLOW_ORIGINS",
        "http://localhost:5173,http://127.0.0.1:5173,"
        "http://localhost:8080,http://127.0.0.1:8080",
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=[origin.strip() for origin in origins.split(",") if origin.strip()],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
        # SSE 需要暴露的响应头
        expose_headers=["X-Accel-Buffering"],
    )

    app.include_router(system_router)
    app.include_router(metadata_router)
    app.include_router(knowledge_router)
    app.include_router(agent_router)
    app.include_router(modeling_router)

    # ------------------------------------------------------------------
    # 全局异常：任何未捕获异常都转成统一信封，避免前端拿到 HTML 错误页
    # ------------------------------------------------------------------
    @app.exception_handler(Exception)
    async def unhandled_exception_handler(request: Request, exc: Exception):
        logger.exception("未处理异常 %s %s", request.method, request.url.path)
        return JSONResponse(status_code=200, content=fail(f"服务内部错误：{exc}"))

    @app.on_event("startup")
    async def on_startup() -> None:
        """启动时预热内核。

        预热失败不阻断启动——服务以 degraded 状态提供 /health，
        便于演示时快速定位是「数据库没起」还是「Agent 没配」。
        """
        from server.deps import get_service

        logger.info("FastAPI 适配层启动，正在预热 Agent 内核…")
        get_service().warmup()
        health = get_service().health()
        if health["agent_ready"]:
            logger.info(
                "内核就绪：数据库 %s，业务表 %s 张，模型 %s",
                health["database_type"],
                health["table_count"],
                health["llm_model"],
            )
        else:
            logger.warning("内核未就绪：%s", health.get("error"))

    @app.get("/", include_in_schema=False)
    def root() -> dict:
        return {
            "name": "企业数据底座智能问析 Agent API",
            "version": "1.0.0",
            "docs": "/docs",
            "health": "/api/v1/system/health",
        }

    return app


app = create_app()


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        app,
        host=os.getenv("API_HOST", "0.0.0.0"),
        port=int(os.getenv("API_PORT", "8000")),
        log_level="info",
    )
