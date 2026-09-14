package com.agent.dto;

import com.fasterxml.jackson.annotation.JsonIgnoreProperties;
import com.fasterxml.jackson.annotation.JsonInclude;
import lombok.AllArgsConstructor;
import lombok.Builder;
import lombok.Data;
import lombok.NoArgsConstructor;

import java.io.Serializable;
import java.util.List;

/**
 * 异常检测请求 DTO。
 *
 * <p>对应上游 {@code POST /api/v1/modeling/anomaly}。
 * 上游 {@code AnomalyRequest} 未对这三个字段声明 alias，
 * 因此全局 {@code SNAKE_CASE} 策略即可正确映射（字段名相同，无下划线）。
 *
 * <p>{@code NON_NULL}：这三个字段在上游都是可选带默认值的，传 {@code null}
 * 会被 pydantic 判为类型不合法（HTTP 422），只有缺省才会落到默认值，
 * 因此让 null 字段不出现在请求体里。
 */
@Data
@Builder
@NoArgsConstructor
@AllArgsConstructor
@JsonIgnoreProperties(ignoreUnknown = true)
@JsonInclude(JsonInclude.Include.NON_NULL)
public class AnomalyRequestDTO implements Serializable {

    private static final long serialVersionUID = 1L;

    /** 参与检测的特征列；为空时由上游自行选择 */
    private List<String> features;

    /** 预期异常比例，上游约束 0.001 ~ 0.5 */
    private Double contamination;

    /** 最多读取行数，上游约束 >= 10 */
    private Integer limit;
}
