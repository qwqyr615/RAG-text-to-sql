package com.agent.exception;

/**
 * Agent 上游服务调用异常。
 *
 * <p>当 Python Agent 内核（FastAPI，默认 {@code http://127.0.0.1:8000}）不可达、
 * 超时或返回非预期响应时抛出。网关会把它转换成可读的
 * {@code Result.error(...)}，而不是向前端抛 500 堆栈。
 */
public class AgentApiException extends BaseException {

    private static final long serialVersionUID = 1L;

    /** 上游 HTTP 状态码；连接层失败时为 {@code null} */
    private final Integer statusCode;

    public AgentApiException(String msg) {
        this(msg, null, null);
    }

    public AgentApiException(String msg, Throwable cause) {
        this(msg, null, cause);
    }

    public AgentApiException(String msg, Integer statusCode, Throwable cause) {
        super(msg, cause);
        this.statusCode = statusCode;
    }

    public Integer getStatusCode() {
        return statusCode;
    }
}
