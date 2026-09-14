package com.agent.vo;

import com.fasterxml.jackson.annotation.JsonIgnoreProperties;
import lombok.AllArgsConstructor;
import lombok.Builder;
import lombok.Data;
import lombok.NoArgsConstructor;

import java.io.Serializable;
import java.util.List;

/**
 * 可建模字段列表 VO。
 *
 * <p>对应上游 {@code GET /api/v1/modeling/features} 的 {@code data}：
 * {@code {fields[], total}}。
 */
@Data
@Builder
@NoArgsConstructor
@AllArgsConstructor
@JsonIgnoreProperties(ignoreUnknown = true)
public class ModelingFeaturesVO implements Serializable {

    private static final long serialVersionUID = 1L;

    /** 字段列表 */
    private List<ModelingFeatureVO> fields;

    /** 字段总数 */
    private Integer total;
}
