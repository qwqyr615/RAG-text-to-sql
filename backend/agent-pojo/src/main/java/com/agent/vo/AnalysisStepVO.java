package com.agent.vo;

import lombok.AllArgsConstructor;
import lombok.Builder;
import lombok.Data;
import lombok.NoArgsConstructor;

import java.io.Serializable;

/**
 * 分析过程步骤 VO。
 *
 * <p>对应上游 {@code AnalysisStep}：{@code {title, detail, kind}}。
 * {@code kind ∈ prompt|sql|result|report|chart|model}，前端据此选择渲染方式。
 */
@Data
@Builder
@NoArgsConstructor
@AllArgsConstructor
public class AnalysisStepVO implements Serializable {

    private static final long serialVersionUID = 1L;

    /** 步骤标题 */
    private String title;

    /** 步骤详情 */
    private String detail;

    /** 步骤类型：prompt / sql / result / report / chart / model */
    private String kind;
}
