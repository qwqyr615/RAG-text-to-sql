package com.agent.vo;

import com.fasterxml.jackson.annotation.JsonIgnoreProperties;
import lombok.AllArgsConstructor;
import lombok.Builder;
import lombok.Data;
import lombok.NoArgsConstructor;

import java.io.Serializable;
import java.util.List;

/**
 * 单张表的元数据 VO。
 *
 * <p>对应上游 {@code GET /api/v1/metadata/tables} 中 {@code data.tables[i]}。
 * 上游结构：{@code {table_name, description, role, grain, primary_key, time_column,
 * columns[], sample_rows, row_count}}。
 *
 * <p>{@code sample_rows} 是任意二维数据，用 {@code List<List<Object>>} 承接；
 * {@code columns} 里的 {@code standard_fields} 也可能是结构化对象，故同样放宽类型。
 */
@Data
@Builder
@NoArgsConstructor
@AllArgsConstructor
@JsonIgnoreProperties(ignoreUnknown = true)
public class TableMetadataVO implements Serializable {

    private static final long serialVersionUID = 1L;

    /** 表名 */
    private String tableName;

    /** 表业务说明 */
    private String description;

    /** 表的角色（如事实表 / 维表） */
    private String role;

    /** 数据粒度说明 */
    private String grain;

    /** 主键字段 */
    private String primaryKey;

    /** 时间字段 */
    private String timeColumn;

    /** 字段清单 */
    private List<ColumnMetadataVO> columns;

    /** 样例数据（行 × 列，结构不固定） */
    private List<List<Object>> sampleRows;

    /** 行数（真实数值，前端据此展示数据规模） */
    private Long rowCount;
}
