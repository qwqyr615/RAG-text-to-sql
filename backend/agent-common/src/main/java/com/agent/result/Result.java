package com.agent.result;

import com.fasterxml.jackson.annotation.JsonInclude;
import lombok.Data;

import java.io.Serializable;

/**
 * 后端统一返回结果。
 *
 * <p>字段与「苍穹外卖」{@code com.sky.result.Result} 完全一致，也与上游 Python
 * Agent 适配层（FastAPI）的信封逐字段对齐，因此网关可以原样透传，前端只需认一种契约：
 *
 * <pre>
 * { "code": 1, "msg": "", "data": { ... } }
 * </pre>
 *
 * <ul>
 *   <li>{@code code = 1} 表示成功</li>
 *   <li>{@code code = 0} 表示失败，此时 {@code msg} 为可读的错误信息</li>
 * </ul>
 *
 * <p>注意：这里显式声明 {@code @JsonInclude(ALWAYS)}，保证即使 {@code data} 为
 * {@code null} 也会输出该字段，避免前端做 {@code 'data' in resp} 之类的判空分支。
 *
 * @param <T> 业务数据类型
 */
@Data
@JsonInclude(JsonInclude.Include.ALWAYS)
public class Result<T> implements Serializable {

    private static final long serialVersionUID = 1L;

    /** 编码：1 成功，0 和其它数字为失败 */
    private Integer code;

    /** 错误信息 */
    private String msg;

    /** 数据 */
    private T data;

    /**
     * 成功，不带数据。
     *
     * @param <T> 业务数据类型
     * @return 成功信封
     */
    public static <T> Result<T> success() {
        Result<T> result = new Result<>();
        result.code = 1;
        result.msg = "";
        return result;
    }

    /**
     * 成功，携带数据。
     *
     * @param object 业务数据
     * @param <T>    业务数据类型
     * @return 成功信封
     */
    public static <T> Result<T> success(T object) {
        Result<T> result = new Result<>();
        result.code = 1;
        result.msg = "";
        result.data = object;
        return result;
    }

    /**
     * 失败。
     *
     * @param msg 错误信息
     * @param <T> 业务数据类型
     * @return 失败信封
     */
    public static <T> Result<T> error(String msg) {
        Result<T> result = new Result<>();
        result.code = 0;
        result.msg = msg;
        return result;
    }

    /**
     * 按上游返回的编码与信息构造信封，用于网关透传。
     *
     * @param code 上游编码（1 成功 / 0 失败）
     * @param msg  上游错误信息
     * @param data 上游业务数据
     * @param <T>  业务数据类型
     * @return 透传信封
     */
    public static <T> Result<T> of(Integer code, String msg, T data) {
        Result<T> result = new Result<>();
        result.code = code == null ? 0 : code;
        result.msg = msg == null ? "" : msg;
        result.data = data;
        return result;
    }
}
