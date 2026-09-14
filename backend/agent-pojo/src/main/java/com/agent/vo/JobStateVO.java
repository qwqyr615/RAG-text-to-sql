package com.agent.vo;

import com.fasterxml.jackson.annotation.JsonIgnore;
import com.fasterxml.jackson.annotation.JsonIgnoreProperties;
import lombok.AllArgsConstructor;
import lombok.Builder;
import lombok.Data;
import lombok.NoArgsConstructor;

import java.io.Serializable;

/**
 * 异步任务状态 / 结果 VO。
 *
 * <p>对应上游 {@code GET /api/v1/agent/jobs/{job_id}} 的 {@code data}，
 * 同时也是 SSE 事件（{@code progress} / {@code done}）的 {@code data} 载荷：
 * {@code {job_id, status, created_at, finished_at, elapsed_ms, progress,
 * stage, result, error}}。
 *
 * <p>{@code result} 为完整的 {@link AskResponseVO}，
 * 因此前端在 SSE 收到 {@code done} 后无需再发一次轮询请求。
 */
@Data
@Builder
@NoArgsConstructor
@AllArgsConstructor
@JsonIgnoreProperties(ignoreUnknown = true)
public class JobStateVO implements Serializable {

    private static final long serialVersionUID = 1L;

    /** 任务 ID */
    private String jobId;

    /** 任务状态：pending / running / succeeded / failed */
    private String status;

    /** 提交时间戳（秒） */
    private Double createdAt;

    /** 完成时间戳（秒） */
    private Double finishedAt;

    /** 耗时毫秒 */
    private Long elapsedMs;

    /** 进度百分比 0-100 */
    private Integer progress;

    /** 当前阶段描述，用于前端进度提示 */
    private String stage;

    /** 成功时的问答结果 */
    private AskResponseVO result;

    /** 失败原因 */
    private String error;

    /**
     * 判断任务是否已进入终态（成功或失败）。
     *
     * <p>{@code @JsonIgnore} 必不可少：Jackson 会把 {@code isXxx()} 这类
     * 布尔 getter 当成属性序列化出去，若不加注解，网关输出里会多出一个上游
     * 没有的 {@code terminal} 字段，破坏「逐字段一致」的透传契约。
     *
     * @return 终态返回 true
     */
    @JsonIgnore
    public boolean isTerminal() {
        return "succeeded".equals(status) || "failed".equals(status);
    }
}
