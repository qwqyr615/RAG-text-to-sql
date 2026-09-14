package com.agent.config;

import lombok.Data;
import org.springframework.boot.context.properties.ConfigurationProperties;

/**
 * 上游 Agent（FastAPI）连接配置。
 *
 * <p>对应 {@code application-dev.yml} 中的 {@code agent.api.*} 配置段。
 * 通过 {@code @ConfigurationProperties} 注入，<b>不在代码里硬编码地址</b>，
 * 便于在测试 / 生产环境切换上游。
 */
@Data
@ConfigurationProperties(prefix = "agent.api")
public class AgentApiProperties {

    /** 上游 FastAPI 基地址，例如 http://127.0.0.1:8000 */
    private String baseUrl = "http://127.0.0.1:8000";

    /** 接口统一前缀，上游为 /api/v1 */
    private String prefix = "/api/v1";

    /** 建立连接超时（毫秒） */
    private int connectTimeout = 10_000;

    /** 读取超时（毫秒）；同步问答最长阻塞 90s，故默认给到 180s */
    private int readTimeout = 180_000;

    // ------------------------------------------------------------------
    // 连接池参数（Apache HttpClient PoolingHttpClientConnectionManager）
    // ------------------------------------------------------------------

    /** 连接池最大连接数 */
    private int maxTotalConnections = 200;

    /** 每个路由（host:port）最大连接数 */
    private int maxConnectionsPerRoute = 50;

    /** 连接存活时间（秒），-1 表示永不过期 */
    private int validateAfterInactivityMillis = 2_000;

    /** 从连接池获取连接的超时（毫秒） */
    private int connectionRequestTimeout = 10_000;
}
