package com.agent.vo;

import com.fasterxml.jackson.annotation.JsonIgnoreProperties;
import lombok.AllArgsConstructor;
import lombok.Builder;
import lombok.Data;
import lombok.NoArgsConstructor;

import java.io.Serializable;

/**
 * 上游服务健康状态 VO。
 *
 * <p>对应上游 {@code GET /api/v1/system/health} 的 {@code data}：
 * {@code {status, agent_ready, database_type, table_count, llm_model,
 * rag_enabled, error}}。
 * 由全局 {@code SNAKE_CASE} 策略完成字段映射。
 */
@Data
@Builder
@NoArgsConstructor
@AllArgsConstructor
@JsonIgnoreProperties(ignoreUnknown = true)
public class HealthVO implements Serializable {

    private static final long serialVersionUID = 1L;

    /** 服务状态：ok / degraded */
    private String status;

    /** Agent 内核是否已就绪（元数据已加载） */
    private Boolean agentReady;

    /** 数据库方言 */
    private String databaseType;

    /** 发现到的业务表数量 */
    private Integer tableCount;

    /** 当前大模型名 */
    private String llmModel;

    /** RAG 段是否启用 */
    private Boolean ragEnabled;

    /** 就绪失败原因 */
    private String error;
}
