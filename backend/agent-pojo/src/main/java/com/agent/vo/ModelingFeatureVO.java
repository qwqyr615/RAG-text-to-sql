package com.agent.vo;

import com.fasterxml.jackson.annotation.JsonIgnoreProperties;
import lombok.AllArgsConstructor;
import lombok.Builder;
import lombok.Data;
import lombok.NoArgsConstructor;

import java.io.Serializable;

/**
 * 可建模字段 VO。
 *
 * <p>对应上游 {@code GET /api/v1/modeling/features} 中 {@code data.fields[i]}：
 * {@code {table, name, type, description, numeric}}。
 */
@Data
@Builder
@NoArgsConstructor
@AllArgsConstructor
@JsonIgnoreProperties(ignoreUnknown = true)
public class ModelingFeatureVO implements Serializable {

    private static final long serialVersionUID = 1L;

    /** 所属表名 */
    private String table;

    /** 字段名 */
    private String name;

    /** 字段类型（已 uppercase） */
    private String type;

    /** 业务说明 */
    private String description;

    /** 是否为数值型（可用于建模） */
    private Boolean numeric;
}
