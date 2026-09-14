package com.agent.controller;

import com.agent.result.Result;
import com.agent.service.AgentApiClient;
import com.agent.vo.HealthVO;
import lombok.extern.slf4j.Slf4j;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RestController;

import java.util.Map;

/**
 * 系统接口。
 *
 * <p>对外路径与上游 FastAPI 完全一致，网关只做转发与信封透传。
 */
@Slf4j
@RestController
@RequestMapping("/api/v1/system")
public class SystemController {

    private final AgentApiClient agentApiClient;

    public SystemController(AgentApiClient agentApiClient) {
        this.agentApiClient = agentApiClient;
    }

    /**
     * 健康检查。
     *
     * <p>返回上游 Agent 内核的就绪状态（元数据是否加载、发现了几张表、
     * 当前模型、RAG 是否启用）。前端与运维可据此做启动探测。
     *
     * @return {@code {code, msg, data:{status, agent_ready, database_type,
     *         table_count, llm_model, rag_enabled, error}}}
     */
    @GetMapping("/health")
    public Result<HealthVO> health() {
        return agentApiClient.health();
    }

    /**
     * 网关自身的存活探针（不依赖上游）。
     *
     * <p>用于区分「Java 网关挂了」与「上游 FastAPI 没起来」这两种故障，
     * 便于排障。路径刻意放在 upstream 没有的 {@code /ping} 上，不会与转发冲突。
     *
     * @return 固定返回 {@code {code:1, data:{gateway:"up"}}}
     */
    @GetMapping("/ping")
    public Result<Map<String, Object>> ping() {
        return Result.success(java.util.Collections.singletonMap("gateway", "up"));
    }
}
