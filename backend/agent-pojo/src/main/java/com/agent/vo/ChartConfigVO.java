package com.agent.vo;

import com.fasterxml.jackson.annotation.JsonIgnoreProperties;
import lombok.AllArgsConstructor;
import lombok.Builder;
import lombok.Data;
import lombok.NoArgsConstructor;

import java.io.Serializable;
import java.util.Map;

/**
 * ECharts 图表配置 VO。
 *
 * <p>对应上游 {@code ChartConfig}：{@code {chart_type, title, option, reason, source}}。
 *
 * <p><b>刻意不做强类型化</b>：{@code option} 是任意深度的 ECharts 配置对象，
 * 用 {@link Map} 原样承接，保证前端拿到的结构与上游完全一致，
 * 也避免上游新增配置项时网关反序列化丢字段。
 */
@Data
@Builder
@NoArgsConstructor
@AllArgsConstructor
@JsonIgnoreProperties(ignoreUnknown = true)
public class ChartConfigVO implements Serializable {

    private static final long serialVersionUID = 1L;

    /** 图表类型：bar / line / pie / scatter / table */
    private String chartType;

    /** 图表标题 */
    private String title;

    /** ECharts option 原始配置（结构不固定，原样透传） */
    private Map<String, Object> option;

    /** 选择该图表的理由 */
    private String reason;

    /** 生成来源：llm / fallback */
    private String source;
}
