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
 * {@code {fields[], total, table, default_features, note}}。
 *
 * <p>注意 {@code table} / {@code defaultFeatures} / {@code note} 三个字段是必需的：
 * 上游只返回**建模模块实际查询的那张表**的字段（数据库里可能同时存在多个数据源，
 * 例如客户原始表用 {@code mot_t}/{@code def_rate} 这类缩写列名）。
 * 前端依赖 {@code table} 告知用户「建模用的是哪张表」，并依赖
 * {@code defaultFeatures} 预选合理的特征集；若此处漏字段，网关会静默丢弃，
 * 前端表现为特征全部未选中、且显示的建模表为空。
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

    /** 建模模块实际查询的表名 */
    private String table;

    /** 上游在各算法下使用的默认特征集，供前端预选 */
    private DefaultFeatures defaultFeatures;

    /** 使用说明（例如「建模只查询某表，其他数据源需先做字段映射」） */
    private String note;

    /**
     * 默认特征集。
     *
     * <p>上游 JSON 键为 {@code default_features}，由
     * {@code AgentApiClient} 使用的 SNAKE_CASE ObjectMapper 映射到本字段。
     */
    @Data
    @Builder
    @NoArgsConstructor
    @AllArgsConstructor
    @JsonIgnoreProperties(ignoreUnknown = true)
    public static class DefaultFeatures implements Serializable {

        private static final long serialVersionUID = 1L;

        /** Isolation Forest 异常检测的默认特征 */
        private List<String> anomaly;

        /** LinearRegression 回归的默认特征 */
        private List<String> regression;
    }
}
