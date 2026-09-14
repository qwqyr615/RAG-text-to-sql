package com.agent.exception;

/**
 * 业务异常基类。
 *
 * <p>所有可预期的业务异常都应继承本类，由全局异常处理器
 * {@code GlobalExceptionHandler} 统一转换成 {@code Result.error(msg)}，
 * 从而避免把堆栈直接暴露给前端。
 */
public class BaseException extends RuntimeException {

    private static final long serialVersionUID = 1L;

    public BaseException() {
    }

    public BaseException(String msg) {
        super(msg);
    }

    public BaseException(String msg, Throwable cause) {
        super(msg, cause);
    }
}
