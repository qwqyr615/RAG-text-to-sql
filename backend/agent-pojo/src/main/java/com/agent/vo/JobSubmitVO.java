package com.agent.vo;

import com.fasterxml.jackson.annotation.JsonIgnoreProperties;
import lombok.AllArgsConstructor;
import lombok.Builder;
import lombok.Data;
import lombok.NoArgsConstructor;

import java.io.Serializable;

/**
 * 异步任务提交结果 VO。
 *
 * <p>对应上游 {@code POST /api/v1/agent/ask/async} 的 {@code data}：
 * {@code {job_id, status}}。
 */
@Data
@Builder
@NoArgsConstructor
@AllArgsConstructor
@JsonIgnoreProperties(ignoreUnknown = true)
public class JobSubmitVO implements Serializable {

    private static final long serialVersionUID = 1L;

    /** 任务 ID，用于轮询或订阅 SSE */
    private String jobId;

    /** 任务状态：pending / running / succeeded / failed */
    private String status;
}
