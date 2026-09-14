package com.agent.vo;

import com.fasterxml.jackson.annotation.JsonIgnoreProperties;
import lombok.AllArgsConstructor;
import lombok.Builder;
import lombok.Data;
import lombok.NoArgsConstructor;

import java.io.Serializable;
import java.util.List;

/**
 * 字段元数据 VO。
 *
 * <p>对应上游 {@code GET /api/v1/metadata/tables} 中
 * {@code data.tables[i].columns[j]}。
 * 上游结构：{@code {name, type, nullable, default, primary_key, description,
 * standard_fields, sample_value}}。
 *
 * <p>{@code standard_fields} 是命中的标准字段口径列表（元素可能是字符串或对象），
 * {@code sample_value} 可能是任意标量，故都用宽松类型承接，绝不丢数据。
 */
@Data
@Builder
@NoArgsConstructor
@AllArgsConstructor
@JsonIgnoreProperties(ignoreUnknown = true)
public class ColumnMetadataVO implements Serializable {

    private static final long serialVersionUID = 1L;

    /** 列名 */
    private String name;

    /** 数据库列类型 */
    private String type;

    /** 是否可空 */
    private Boolean nullable;

    /** 默认值 */
    private String defaultValue;

    /** 是否主键 */
    private Boolean primaryKey;

    /** 业务含义说明 */
    private String description;

    /** 命中的标准字段口径（元素结构不固定，用 Object 承接） */
    private List<Object> standardFields;

    /** 样例值 */
    private Object sampleValue;
}
