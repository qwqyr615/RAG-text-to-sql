package com.agent.dto;

import com.fasterxml.jackson.annotation.JsonAlias;
import com.fasterxml.jackson.annotation.JsonIgnoreProperties;
import com.fasterxml.jackson.annotation.JsonInclude;
import com.fasterxml.jackson.annotation.JsonProperty;
import lombok.AllArgsConstructor;
import lombok.Builder;
import lombok.Data;
import lombok.NoArgsConstructor;

import java.io.Serializable;
import java.util.List;

/**
 * 线性回归建模请求 DTO。
 *
 * <p>对应上游 {@code POST /api/v1/modeling/regression}。
 * 上游 {@code RegressionRequest} 对 {@code test_size} 声明了 alias {@code testSize}，
 * 显式 alias 优先，故此处用 {@link JsonProperty} 强制 camelCase，
 * 并用 {@link JsonAlias} 兼容两种入参写法。
 *
 * <p>{@code NON_NULL}：{@code features} / {@code limit} 在上游是可选带默认值的，
 * 传 {@code null} 会被 pydantic 判为类型不合法（HTTP 422），
 * 因此让 null 字段不出现在请求体里，由上游落到默认值。
 */
@Data
@Builder
@NoArgsConstructor
@AllArgsConstructor
@JsonIgnoreProperties(ignoreUnknown = true)
@JsonInclude(JsonInclude.Include.NON_NULL)
public class RegressionRequestDTO implements Serializable {

    private static final long serialVersionUID = 1L;

    /** 目标列，默认 defect_rate */
    private String target;

    /** 特征列；为空时由上游自行选择 */
    private List<String> features;

    /** 最多读取行数，上游约束 >= 30 */
    private Integer limit;

    /** 测试集比例，上游约束 (0, 1) */
    @JsonProperty("testSize")
    @JsonAlias({"test_size", "testsize"})
    private Double testSize;
}
