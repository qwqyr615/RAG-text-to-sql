package com.agent.vo;

import com.fasterxml.jackson.annotation.JsonIgnoreProperties;
import lombok.AllArgsConstructor;
import lombok.Builder;
import lombok.Data;
import lombok.NoArgsConstructor;

import java.io.Serializable;
import java.util.List;
import java.util.Map;

/**
 * 问答 / 报告 / 建模分析结果 VO。
 *
 * <p>对应上游 {@code AskResponse}（{@code server/schemas.py}），
 * 是 {@code /agent/ask}、{@code /agent/report} 的 {@code data}，
 * 以及 {@code /agent/jobs/{job_id}} 的 {@code data.result}。
 *
 * <h3>反序列化要点</h3>
 * 全局 Jackson 策略为 {@code SNAKE_CASE}，因此本类字段名一律用 Java 驼峰，
 * 由策略负责映射到上游的下划线字段：
 * {@code task_type}、{@code session_id}、{@code row_count}、
 * {@code chart_config}、{@code analysis_text}、{@code rag_context}、
 * {@code prompt_usage}、{@code analysis_steps}、{@code metric_bindings}、
 * {@code sql_error} 等，不需要逐字段写 {@code @JsonProperty}。
 *
 * <p>结构不固定的字段（ECharts 配置、表格行、Prompt 用量）一律用
 * {@code Map}/{@code List} 承接，不做强类型化，避免因上游扩展字段而丢数据；
 * 再加上 {@code @JsonIgnoreProperties(ignoreUnknown = true)} 做兜底。
 */
@Data
@Builder
@NoArgsConstructor
@AllArgsConstructor
@JsonIgnoreProperties(ignoreUnknown = true)
public class AskResponseVO implements Serializable {

    private static final long serialVersionUID = 1L;

    /** 任务类型：sql_query / report / anomaly / regression */
    private String taskType;

    /** 本次分析是否成功 */
    private Boolean success;

    /** 原始问题 */
    private String question;

    /** 会话 ID */
    private String sessionId;

    /** 本次注入的历史轮数 */
    private Integer turnsUsed;

    /** 生成的 SQL，前端可展示 */
    private String sql;

    /** 结果列名 */
    private List<String> columns;

    /** 结果数据（二维数组，行 × 列） */
    private List<List<Object>> rows;

    /** 返回行数，等于 rows 长度 */
    private Integer rowCount;

    /** 文字分析结论 */
    private String analysisText;

    /** Markdown 报告（task_type=report 时有值） */
    private String report;

    /** ECharts 图表配置；option 为任意结构，用 Map 承接 */
    private ChartConfigVO chartConfig;

    /** RAG 命中示例 */
    private String ragContext;

    /** Prompt 各段预算用量；结构不固定，用 Map 承接 */
    private Map<String, Object> promptUsage;

    /** 分析过程步骤，供前端展示推理链路 */
    private List<AnalysisStepVO> analysisSteps;

    /** 命中的业务指标口径 */
    private List<Map<String, Object>> metricBindings;

    /** 错误信息 */
    private String error;

    /** 结果回放取数失败原因（分析结论仍有效） */
    private String sqlError;
}
