package com.agent.constant;

/**
 * 全局常量。
 *
 * <p>集中管理网关与上游 Agent 契约之间的固定约定，避免魔法值散落各处。
 */
public final class AgentConstants {

    private AgentConstants() {
    }

    // ------------------------------------------------------------------
    // 统一信封（与上游 FastAPI 适配层 server/schemas.py 保持一致）
    // ------------------------------------------------------------------

    /** 上游信封中 code=1 表示成功 */
    public static final int ENVELOPE_SUCCESS_CODE = 1;

    /** 上游信封中 code=0 表示失败 */
    public static final int ENVELOPE_FAILURE_CODE = 0;

    /** 成功时上游返回的空错误信息 */
    public static final String EMPTY_MSG = "";

    // ------------------------------------------------------------------
    // 异步任务状态（上游 JobStateResponse.status）
    // ------------------------------------------------------------------

    public static final String JOB_STATUS_PENDING = "pending";
    public static final String JOB_STATUS_RUNNING = "running";
    public static final String JOB_STATUS_SUCCEEDED = "succeeded";
    public static final String JOB_STATUS_FAILED = "failed";

    // ------------------------------------------------------------------
    // SSE 事件名（上游 /agent/jobs/{id}/stream 约定）
    // ------------------------------------------------------------------

    public static final String SSE_EVENT_PROGRESS = "progress";
    public static final String SSE_EVENT_DONE = "done";

    // ------------------------------------------------------------------
    // 会话默认值
    // ------------------------------------------------------------------

    /** 未显式传 sessionId 时使用的默认会话 */
    public static final String DEFAULT_SESSION_ID = "default";

    // ------------------------------------------------------------------
    // 错误提示
    // ------------------------------------------------------------------

    /**
     * 上游未启动时给前端的可读提示。
     *
     * @return 中文提示
     */
    public static String MSG_SERVICE_UNAVAILABLE() {
        return "Agent 服务不可用，请确认 FastAPI 已在 127.0.0.1:8000 启动";
    }

    /**
     * 上游调用超时提示。
     *
     * @return 中文提示
     */
    public static String MSG_SERVICE_TIMEOUT() {
        return "Agent 服务响应超时，请稍后重试或改用异步接口";
    }

    /**
     * 上游返回体无法解析时的提示。
     *
     * @return 中文提示
     */
    public static String MSG_BAD_UPSTREAM_RESPONSE() {
        return "Agent 服务返回了无法解析的响应";
    }
}
