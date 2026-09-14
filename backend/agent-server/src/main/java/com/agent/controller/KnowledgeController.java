package com.agent.controller;

import com.agent.result.Result;
import com.agent.service.AgentApiClient;
import lombok.extern.slf4j.Slf4j;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RestController;

import java.util.Map;

/**
 * 业务知识管理接口。
 *
 * <p>对应上游 {@code /api/v1/knowledge/*}：分析主题、业务对象、指标规则与知识图谱。
 */
@Slf4j
@RestController
@RequestMapping("/api/v1/knowledge")
public class KnowledgeController {

    private final AgentApiClient agentApiClient;

    public KnowledgeController(AgentApiClient agentApiClient) {
        this.agentApiClient = agentApiClient;
    }

    /**
     * 业务知识总览：分析主题、业务对象、指标规则。
     *
     * @return {@code {code, msg, data:{schema_version, themes[], objects[], rules[], graph}}}
     */
    @GetMapping("/overview")
    public Result<Map<String, Object>> overview() {
        return agentApiClient.knowledgeOverview();
    }

    /**
     * 逻辑知识图谱：主题 → 业务对象 → 指标 → 字段 → 数据表。
     *
     * @return {@code {code, msg, data:{nodes[], edges[], categories[]}}}
     */
    @GetMapping("/graph")
    public Result<Map<String, Object>> graph() {
        return agentApiClient.knowledgeGraph();
    }
}
