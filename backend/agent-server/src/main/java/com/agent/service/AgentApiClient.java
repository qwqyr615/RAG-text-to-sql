package com.agent.service;

import com.agent.config.AgentApiProperties;
import com.agent.constant.AgentConstants;
import com.agent.dto.AgentAskDTO;
import com.agent.dto.AnomalyRequestDTO;
import com.agent.dto.RegressionRequestDTO;
import com.agent.exception.AgentApiException;
import com.agent.result.Result;
import com.agent.util.JsonUtils;
import com.agent.vo.AskResponseVO;
import com.agent.vo.HealthVO;
import com.agent.vo.JobStateVO;
import com.agent.vo.JobSubmitVO;
import com.agent.vo.ModelingFeaturesVO;
import com.agent.vo.SessionResetVO;
import com.fasterxml.jackson.core.type.TypeReference;
import com.fasterxml.jackson.databind.ObjectMapper;
import lombok.extern.slf4j.Slf4j;
import org.springframework.core.io.Resource;
import org.springframework.http.HttpEntity;
import org.springframework.http.HttpHeaders;
import org.springframework.http.HttpMethod;
import org.springframework.http.MediaType;
import org.springframework.http.ResponseEntity;
import org.springframework.http.client.ClientHttpResponse;
import org.springframework.stereotype.Component;
import org.springframework.web.client.ResourceAccessException;
import org.springframework.web.client.ResponseErrorHandler;
import org.springframework.web.client.RestClientException;
import org.springframework.web.client.RestTemplate;
import org.springframework.web.util.UriComponentsBuilder;

import jakarta.annotation.PostConstruct;
import java.net.ConnectException;
import java.net.SocketTimeoutException;
import java.net.URI;
import java.util.Collections;
import java.util.Map;

/**
 * 上游 Agent（FastAPI 适配层）HTTP 客户端。
 *
 * <p>这是网关与 Python Agent 内核之间<b>唯一的</b>通信出口：所有 Controller
 * 都只做参数校验与转发，真正的调用细节（URL 拼装、超时、错误兜底、信封透传）
 * 全部收敛在本类，便于后续替换实现（例如换成 WebClient 或加熔断）。
 *
 * <h3>契约</h3>
 * 上游所有响应都是统一信封 {@code {code, msg, data}}：
 * <ul>
 *   <li>{@code code = 1} 成功，{@code data} 为业务数据；</li>
 *   <li>{@code code = 0} 失败，{@code msg} 为错误信息。</li>
 * </ul>
 * 本类把信封解出来，再用 {@link Result#of} 还原成 {@code Result<T>}，
 * 从而保证网关对外的 JSON 与上游<b>逐字段一致</b>，前端只认一种契约。
 *
 * <h3>错误兜底</h3>
 * 上游未启动、连接被拒、读超时等异常都不会向上冒泡成 500，
 * 而是转换成 {@code Result.error(可读中文提示)}，由 Controller 直接返回。
 */
@Slf4j
@Component
public class AgentApiClient {

    private final RestTemplate restTemplate;
    private final AgentApiProperties properties;
    private final ObjectMapper objectMapper = JsonUtils.mapper();

    public AgentApiClient(RestTemplate agentRestTemplate, AgentApiProperties properties) {
        this.restTemplate = agentRestTemplate;
        this.properties = properties;
    }

    /**
     * 关闭 RestTemplate 默认的「非 2xx 抛异常」行为。
     *
     * <p>上游在业务失败时仍返回 HTTP 200（信封里 {@code code=0}），但 404（任务不存在）
     * 之类的状态码也带有可读的 JSON body。若让默认 {@code DefaultResponseErrorHandler}
     * 抛异常，就会丢掉这些信息，所以这里改为完全手动判断。
     */
    @PostConstruct
    public void customizeErrorHandler() {
        restTemplate.setErrorHandler(new ResponseErrorHandler() {
            @Override
            public boolean hasError(ClientHttpResponse response) {
                return false;
            }

            @Override
            public void handleError(ClientHttpResponse response) {
                // 不抛异常：状态码与响应体交给调用方处理
            }
        });
    }

    // ==================================================================
    // 系统
    // ==================================================================

    /**
     * 健康检查。
     *
     * @return 上游健康状态信封
     */
    public Result<HealthVO> health() {
        return exchange("/system/health", HttpMethod.GET, null, new TypeReference<HealthVO>() {
        });
    }

    // ==================================================================
    // 数据资源理解（元数据）
    // ==================================================================

    /**
     * 获取全部表结构 / 字段 / 样例值。
     *
     * <p>返回类型刻意用 {@code Map<String,Object>} 而不是强类型 VO：
     * data 里含 {@code relationships}、{@code field_map}、{@code metric_bindings}、
     * {@code inventory} 等结构不固定的字段，用 Map 能保证<b>一个字节都不丢</b>地透传，
     * 这是「前端只认一种契约」的关键。
     *
     * @return 元数据信封
     */
    public Result<Map<String, Object>> metadataTables() {
        return exchange("/metadata/tables", HttpMethod.GET, null, mapType());
    }

    /**
     * 获取表间关系。
     *
     * @return 关系信封
     */
    public Result<Map<String, Object>> metadataRelationships() {
        return exchange("/metadata/relationships", HttpMethod.GET, null, mapType());
    }

    /**
     * 获取标准字段口径映射。
     *
     * @return 字段映射信封
     */
    public Result<Map<String, Object>> metadataFieldMap() {
        return exchange("/metadata/field-map", HttpMethod.GET, null, mapType());
    }

    // ==================================================================
    // 业务知识管理
    // ==================================================================

    /**
     * 获取主题 / 对象 / 规则总览。
     *
     * @return 知识总览信封
     */
    public Result<Map<String, Object>> knowledgeOverview() {
        return exchange("/knowledge/overview", HttpMethod.GET, null, mapType());
    }

    /**
     * 获取知识图谱节点与边。
     *
     * @return 知识图谱信封
     */
    public Result<Map<String, Object>> knowledgeGraph() {
        return exchange("/knowledge/graph", HttpMethod.GET, null, mapType());
    }

    // ==================================================================
    // 智能分析
    // ==================================================================

    /**
     * 同步自然语言问答。
     *
     * <p>上游最长阻塞 90 秒，网关读取超时设为 180 秒（{@code agent.api.read-timeout}）。
     *
     * @param request 问答请求
     * @return 问答结果信封
     */
    public Result<AskResponseVO> ask(AgentAskDTO request) {
        return exchange("/agent/ask", HttpMethod.POST, request, new TypeReference<AskResponseVO>() {
        });
    }

    /**
     * 生成 Markdown 报告（上游内部会先执行查询再生成报告）。
     *
     * @param request 问答请求
     * @return 带 report 字段的结果信封
     */
    public Result<AskResponseVO> report(AgentAskDTO request) {
        return exchange("/agent/report", HttpMethod.POST, request, new TypeReference<AskResponseVO>() {
        });
    }

    /**
     * 提交异步问答任务，立即返回 job_id。
     *
     * @param request 问答请求
     * @return 任务提交信封
     */
    public Result<JobSubmitVO> askAsync(AgentAskDTO request) {
        return exchange("/agent/ask/async", HttpMethod.POST, request, new TypeReference<JobSubmitVO>() {
        });
    }

    /**
     * 查询异步任务状态与结果。
     *
     * @param jobId 任务 ID
     * @return 任务状态信封
     */
    public Result<JobStateVO> job(String jobId) {
        return exchange("/agent/jobs/{jobId}", HttpMethod.GET, null, new TypeReference<JobStateVO>() {
        }, jobId);
    }

    /**
     * 清空指定会话的多轮历史。
     *
     * @param sessionId 会话 ID
     * @return 重置结果信封
     */
    public Result<SessionResetVO> resetSession(String sessionId) {
        return exchange("/agent/session/{sessionId}", HttpMethod.DELETE, null,
                new TypeReference<SessionResetVO>() {
                }, sessionId);
    }

    // ==================================================================
    // 机器学习建模
    // ==================================================================

    /**
     * 异常检测（Isolation Forest）。
     *
     * @param request 检测请求
     * @return 检测结果信封（结构由上游决定，用 Map 承接）
     */
    public Result<Map<String, Object>> anomaly(AnomalyRequestDTO request) {
        return exchange("/modeling/anomaly", HttpMethod.POST, request, mapType());
    }

    /**
     * 线性回归建模。
     *
     * @param request 建模请求
     * @return 建模结果信封（含 coefficients 等不固定结构，用 Map 承接）
     */
    public Result<Map<String, Object>> regression(RegressionRequestDTO request) {
        return exchange("/modeling/regression", HttpMethod.POST, request, mapType());
    }

    /**
     * 列出可用于建模的字段。
     *
     * @param role 过滤条件：numeric / all，可为 null
     * @return 字段列表信封
     */
    public Result<ModelingFeaturesVO> features(String role) {
        String path = "/modeling/features";
        if (role != null && !role.trim().isEmpty()) {
            path = path + "?role={role}";
            return exchange(path, HttpMethod.GET, null, new TypeReference<ModelingFeaturesVO>() {
            }, role);
        }
        return exchange(path, HttpMethod.GET, null, new TypeReference<ModelingFeaturesVO>() {
        });
    }

    // ==================================================================
    // SSE 流代理
    // ==================================================================

    /**
     * 订阅上游 SSE 任务进度流。
     *
     * <p>返回原始 {@link ResponseEntity} 而不是解析后的对象，因为网关需要用
     * {@code SseEmitter} 把上游的 {@code progress} / {@code done} 事件
     * <b>逐帧转发</b>给浏览器，中间不能做 JSON 重组。
     *
     * @param jobId 任务 ID
     * @return 上游的流式响应（body 为 {@link Resource}）
     */
    public ResponseEntity<Resource> openJobStream(String jobId) {
        URI uri = buildUri("/agent/jobs/{jobId}/stream", jobId);
        HttpHeaders headers = new HttpHeaders();
        headers.setAccept(Collections.singletonList(MediaType.TEXT_EVENT_STREAM));
        try {
            log.debug("代理上游 SSE：GET {}", uri);
            return restTemplate.exchange(uri, HttpMethod.GET, new HttpEntity<>(headers), Resource.class);
        } catch (Exception e) {
            throw translate(e, "订阅任务进度流失败");
        }
    }

    // ==================================================================
    // 通用请求执行 + 信封还原
    // ==================================================================

    /**
     * 执行一次上游请求，并把上游 {@code {code,msg,data}} 信封还原成 {@link Result}。
     *
     * @param path          相对于 {@code agent.api.prefix} 的路径，可含 {@code {name}} 占位符
     * @param method        HTTP 方法
     * @param body          请求体，GET/DELETE 传 null
     * @param typeReference data 的目标类型
     * @param uriVariables  路径占位符变量
     * @param <T>           data 类型
     * @return 还原后的统一返回结果
     */
    private <T> Result<T> exchange(String path,
                                   HttpMethod method,
                                   Object body,
                                   TypeReference<T> typeReference,
                                   Object... uriVariables) {
        URI uri = buildUri(path, uriVariables);
        HttpEntity<?> entity = buildEntity(body);

        try {
            log.debug("调用上游：{} {}", method, uri);
            ResponseEntity<String> response =
                    restTemplate.exchange(uri, method, entity, String.class);

            int status = response.getStatusCode().value();
            String raw = response.getBody();
            log.debug("上游响应：{} {} -> HTTP {}，body 长度={}", method, uri, status,
                    raw == null ? 0 : raw.length());

            return parseEnvelope(raw, status, typeReference);

        } catch (ResourceAccessException e) {
            // 连接被拒 / 读超时 / DNS 失败，都在这里
            throw translate(e, "调用上游 Agent 失败：" + uri);
        } catch (RestClientException e) {
            throw new AgentApiException("调用上游 Agent 失败：" + e.getMessage(), e);
        }
    }

    /**
     * 解析上游返回的统一信封。
     *
     * @param raw           上游原始 JSON 文本
     * @param status        HTTP 状态码，用于生成可读错误信息
     * @param typeReference data 字段的目标类型
     * @param <T>           data 类型
     * @return 还原后的统一返回结果
     */
    private <T> Result<T> parseEnvelope(String raw, int status, TypeReference<T> typeReference) {
        if (raw == null || raw.trim().isEmpty()) {
            if (status >= 200 && status < 300) {
                return Result.success();
            }
            return Result.error(statusMessage(status));
        }

        Map<String, Object> envelope;
        try {
            envelope = objectMapper.readValue(raw, new TypeReference<Map<String, Object>>() {
            });
        } catch (Exception e) {
            // 上游返回了非 JSON（例如 Nginx 错误页、traceback 文本）
            log.warn("上游响应不是合法 JSON 信封（HTTP {}）：{}", status, abbreviate(raw));
            if (status >= 200 && status < 300) {
                throw new AgentApiException(AgentConstants.MSG_BAD_UPSTREAM_RESPONSE() + "："
                        + abbreviate(raw), e);
            }
            throw new AgentApiException(statusMessage(status), e);
        }

        // code 缺失时按 HTTP 状态码兜底判断，保证永远不会出现 code=null
        Object codeValue = envelope.get("code");
        int code = codeValue instanceof Number
                ? ((Number) codeValue).intValue()
                : (status >= 200 && status < 300 ? AgentConstants.ENVELOPE_SUCCESS_CODE
                : AgentConstants.ENVELOPE_FAILURE_CODE);

        Object msgValue = envelope.get("msg");
        String msg = msgValue == null ? "" : String.valueOf(msgValue);

        // 失败且上游没给 msg 时，补一个可读提示，避免前端拿到空错误
        if (code != AgentConstants.ENVELOPE_SUCCESS_CODE && msg.isEmpty()) {
            msg = statusMessage(status);
        }

        Object data = envelope.get("data");
        T typed = null;
        if (data != null) {
            typed = objectMapper.convertValue(data, typeReference);
        }
        return Result.of(code, msg, typed);
    }

    /**
     * 构造请求实体：JSON 请求体 + UTF-8 编码。
     *
     * @param body 请求体对象，可为 null
     * @return HttpEntity
     */
    private HttpEntity<?> buildEntity(Object body) {
        HttpHeaders headers = new HttpHeaders();
        headers.setContentType(MediaType.APPLICATION_JSON);
        headers.setAccept(Collections.singletonList(MediaType.APPLICATION_JSON));
        // 上游声明响应为 snake_case；请求体字段名由 AgentAskDTO 上的 @JsonProperty 决定
        return body == null ? new HttpEntity<>(headers) : new HttpEntity<>(body, headers);
    }

    /**
     * 拼接上游完整 URI（基地址 + 前缀 + 路径 + 查询串）。
     *
     * @param path         相对路径
     * @param uriVariables 占位符变量
     * @return 完整 URI
     */
    private URI buildUri(String path, Object... uriVariables) {
        String base = trimTrailingSlash(properties.getBaseUrl());
        String prefix = properties.getPrefix() == null ? "" : properties.getPrefix();
        prefix = trimTrailingSlash(prefix.startsWith("/") ? prefix : "/" + prefix);
        String fullPath = path.startsWith("/") ? path : "/" + path;

        UriComponentsBuilder builder = UriComponentsBuilder.fromHttpUrl(base + prefix + fullPath);
        return uriVariables == null || uriVariables.length == 0
                ? builder.build(true).toUri()
                : builder.buildAndExpand(uriVariables).encode().toUri();
    }

    /**
     * 去掉结尾的斜杠，避免出现 {@code //api/v1} 这类路径。
     *
     * @param value 原始字符串
     * @return 处理后的字符串
     */
    private String trimTrailingSlash(String value) {
        if (value == null) {
            return "";
        }
        String result = value.trim();
        while (result.endsWith("/")) {
            result = result.substring(0, result.length() - 1);
        }
        return result;
    }

    /**
     * 把底层网络异常翻译成对前端友好的 {@link AgentApiException}。
     *
     * @param e       原始异常
     * @param context 上下文描述
     * @return 统一异常
     */
    private AgentApiException translate(Exception e, String context) {
        Throwable cause = e;
        while (cause != null) {
            if (cause instanceof SocketTimeoutException) {
                log.warn("调用上游超时：{}", context);
                return new AgentApiException(AgentConstants.MSG_SERVICE_TIMEOUT(), e);
            }
            if (cause instanceof ConnectException) {
                log.warn("无法连接上游：{} -> {}", context, cause.getMessage());
                return new AgentApiException(AgentConstants.MSG_SERVICE_UNAVAILABLE(), e);
            }
            cause = cause.getCause();
        }
        log.warn("调用上游异常：{} -> {}", context, e.toString());
        return new AgentApiException(AgentConstants.MSG_SERVICE_UNAVAILABLE() + "（" + context + "）", e);
    }

    /**
     * 按 HTTP 状态码生成中文提示。
     *
     * @param status HTTP 状态码
     * @return 中文提示
     */
    private String statusMessage(int status) {
        if (status == 404) {
            return "上游 Agent 未找到对应资源（HTTP 404）";
        }
        if (status == 422) {
            return "上游 Agent 校验请求参数失败（HTTP 422）";
        }
        if (status >= 500) {
            return "上游 Agent 内部错误（HTTP " + status + "）";
        }
        return "上游 Agent 返回异常状态（HTTP " + status + "）";
    }

    /**
     * 截断过长的异常文本，避免把整页 HTML 塞进日志或响应。
     *
     * @param text 原始文本
     * @return 截断后的文本
     */
    private String abbreviate(String text) {
        if (text == null) {
            return "";
        }
        String flat = text.replaceAll("\\s+", " ").trim();
        return flat.length() <= 200 ? flat : flat.substring(0, 200) + "...";
    }

    /**
     * {@code Map<String,Object>} 的 TypeReference 工厂。
     *
     * @return 泛型引用
     */
    private TypeReference<Map<String, Object>> mapType() {
        return new TypeReference<Map<String, Object>>() {
        };
    }
}
