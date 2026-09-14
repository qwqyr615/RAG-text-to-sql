package com.agent.handler;

import com.agent.constant.AgentConstants;
import com.agent.exception.AgentApiException;
import com.agent.exception.BaseException;
import com.agent.result.Result;
import lombok.extern.slf4j.Slf4j;
import org.springframework.http.converter.HttpMessageNotReadableException;
import org.springframework.web.HttpRequestMethodNotSupportedException;
import org.springframework.web.bind.MethodArgumentNotValidException;
import org.springframework.web.bind.MissingServletRequestParameterException;
import org.springframework.web.bind.annotation.ExceptionHandler;
import org.springframework.web.bind.annotation.RestControllerAdvice;
import org.springframework.web.method.annotation.MethodArgumentTypeMismatchException;
import org.springframework.web.servlet.NoHandlerFoundException;

import jakarta.servlet.http.HttpServletRequest;

/**
 * 全局异常处理器。
 *
 * <p>保证网关<b>永远返回统一信封</b>，且不把 Java 堆栈暴露给前端：
 * 任何异常都会被翻译成 {@code {code:0, msg:"可读中文原因", data:null}}。
 * 这样前端的错误处理逻辑只需判断 {@code code}，不需要区分 HTTP 状态码。
 */
@Slf4j
@RestControllerAdvice
public class GlobalExceptionHandler {

    /**
     * 业务异常（含参数校验失败）。
     *
     * @param ex      业务异常
     * @param request 当前请求，用于日志定位
     * @return 失败信封
     */
    @ExceptionHandler(BaseException.class)
    public Result<Void> handleBaseException(BaseException ex, HttpServletRequest request) {
        log.warn("业务异常 [{} {}]：{}", request.getMethod(), request.getRequestURI(), ex.getMessage());
        return Result.error(ex.getMessage());
    }

    /**
     * 上游 Agent 服务异常（不可达 / 超时 / 响应异常）。
     *
     * <p>等价于 {@link BaseException} 分支，单独声明是为了让日志级别与语义更清晰，
     * 也便于将来接入告警（例如连续多次「服务不可用」触发通知）。
     *
     * @param ex      上游调用异常
     * @param request 当前请求
     * @return 失败信封
     */
    @ExceptionHandler(AgentApiException.class)
    public Result<Void> handleAgentApiException(AgentApiException ex, HttpServletRequest request) {
        log.error("上游 Agent 调用失败 [{} {}]：{}", request.getMethod(), request.getRequestURI(), ex.getMessage());
        String msg = ex.getMessage() == null || ex.getMessage().trim().isEmpty()
                ? AgentConstants.MSG_SERVICE_UNAVAILABLE()
                : ex.getMessage();
        return Result.error(msg);
    }

    /**
     * 请求体缺失或 JSON 格式错误。
     *
     * @param ex 解析异常
     * @return 失败信封
     */
    @ExceptionHandler(HttpMessageNotReadableException.class)
    public Result<Void> handleNotReadable(HttpMessageNotReadableException ex) {
        log.warn("请求体不可读：{}", ex.getMessage());
        return Result.error("请求体不是合法 JSON 或缺少请求体");
    }

    /**
     * {@code @RequestBody} 上的 Bean Validation 失败（预留）。
     *
     * @param ex 校验异常
     * @return 失败信封
     */
    @ExceptionHandler(MethodArgumentNotValidException.class)
    public Result<Void> handleValidation(MethodArgumentNotValidException ex) {
        String msg = ex.getBindingResult().getFieldErrors().stream()
                .findFirst()
                .map(error -> error.getField() + " " + error.getDefaultMessage())
                .orElse("请求参数校验失败");
        log.warn("参数校验失败：{}", msg);
        return Result.error(msg);
    }

    /**
     * 缺少必填查询参数。
     *
     * @param ex 缺参异常
     * @return 失败信封
     */
    @ExceptionHandler(MissingServletRequestParameterException.class)
    public Result<Void> handleMissingParam(MissingServletRequestParameterException ex) {
        return Result.error("缺少必填参数：" + ex.getParameterName());
    }

    /**
     * 参数类型不匹配（例如把非数字传给了 Integer 参数）。
     *
     * @param ex 类型异常
     * @return 失败信封
     */
    @ExceptionHandler(MethodArgumentTypeMismatchException.class)
    public Result<Void> handleTypeMismatch(MethodArgumentTypeMismatchException ex) {
        return Result.error("参数 " + ex.getName() + " 类型不正确");
    }

    /**
     * 请求方法不支持。
     *
     * @param ex 方法异常
     * @return 失败信封
     */
    @ExceptionHandler(HttpRequestMethodNotSupportedException.class)
    public Result<Void> handleMethodNotSupported(HttpRequestMethodNotSupportedException ex) {
        return Result.error("不支持的请求方法：" + ex.getMethod());
    }

    /**
     * 路径不存在（需要配置 {@code spring.mvc.throw-exception-if-no-handler-found}）。
     *
     * @param ex 404 异常
     * @return 失败信封
     */
    @ExceptionHandler(NoHandlerFoundException.class)
    public Result<Void> handleNoHandler(NoHandlerFoundException ex) {
        return Result.error("接口不存在：" + ex.getRequestURL());
    }

    /**
     * 兜底异常处理。
     *
     * <p>这是最后一道防线：任何未预期异常都在这里转成可读信封，
     * 避免前端拿到 Spring 默认的 Whitelabel Error Page 或 HTML 错误页。
     * 完整堆栈会打进日志，方便排障。
     *
     * @param ex      异常
     * @param request 当前请求
     * @return 失败信封
     */
    @ExceptionHandler(Exception.class)
    public Result<Void> handleException(Exception ex, HttpServletRequest request) {
        log.error("系统异常 [{} {}]", request.getMethod(), request.getRequestURI(), ex);
        return Result.error("服务内部异常：" + ex.getClass().getSimpleName());
    }
}
