package com.agent.controller;

import com.agent.result.Result;
import com.agent.service.AgentApiClient;
import lombok.extern.slf4j.Slf4j;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RestController;

import java.util.Map;

/**
 * 数据资源理解（元数据）接口。
 *
 * <p>对应上游 {@code /api/v1/metadata/*}。
 *
 * <h3>为什么返回 Map 而不是强类型 VO</h3>
 * {@code /metadata/tables} 的 data 里含 {@code relationships}、{@code field_map}、
 * {@code metric_bindings}、{@code unmatched_metrics}、{@code inventory} 等
 * 结构随业务演进的字段。用 {@code Map<String,Object>} 承接可以
 * <b>保证原样透传、一个字段都不丢</b>，这是「前端只认一种契约」的前提；
 * 而 {@code agent-pojo} 里仍保留 {@code TableMetadataVO}/{@code ColumnMetadataVO}
 * 作为字段结构的可读文档与强类型调用入口。
 */
@Slf4j
@RestController
@RequestMapping("/api/v1/metadata")
public class MetadataController {

    private final AgentApiClient agentApiClient;

    public MetadataController(AgentApiClient agentApiClient) {
        this.agentApiClient = agentApiClient;
    }

    /**
     * 获取全部业务表、字段、类型、说明、样例值。
     *
     * @return {@code {code, msg, data:{schema_version, database_type, tables[],
     *         relationships, field_map, metric_bindings, unmatched_metrics, inventory}}}
     */
    @GetMapping("/tables")
    public Result<Map<String, Object>> tables() {
        return agentApiClient.metadataTables();
    }

    /**
     * 获取表间关系。
     *
     * <p>注意：当前数据模型是单表宽表，{@code relationships} 通常为空；
     * 知识图谱请改用 {@code GET /api/v1/knowledge/graph}。
     *
     * @return {@code {code, msg, data:{relationships, database_type, note}}}
     */
    @GetMapping("/relationships")
    public Result<Map<String, Object>> relationships() {
        return agentApiClient.metadataRelationships();
    }

    /**
     * 获取标准字段口径映射（含单位换算与枚举取值）。
     *
     * @return {@code {code, msg, data:{field_map, metric_bindings,
     *         unmatched_metrics, mapping_profile}}}
     */
    @GetMapping("/field-map")
    public Result<Map<String, Object>> fieldMap() {
        return agentApiClient.metadataFieldMap();
    }
}
