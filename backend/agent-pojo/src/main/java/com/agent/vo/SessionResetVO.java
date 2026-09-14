package com.agent.vo;

import com.fasterxml.jackson.annotation.JsonIgnoreProperties;
import lombok.AllArgsConstructor;
import lombok.Builder;
import lombok.Data;
import lombok.NoArgsConstructor;

import java.io.Serializable;

/**
 * 会话重置结果 VO。
 *
 * <p>对应上游 {@code DELETE /api/v1/agent/session/{session_id}} 的 {@code data}：
 * {@code {session_id, reset}}。
 */
@Data
@Builder
@NoArgsConstructor
@AllArgsConstructor
@JsonIgnoreProperties(ignoreUnknown = true)
public class SessionResetVO implements Serializable {

    private static final long serialVersionUID = 1L;

    /** 会话 ID */
    private String sessionId;

    /** 是否已重置 */
    private Boolean reset;
}
