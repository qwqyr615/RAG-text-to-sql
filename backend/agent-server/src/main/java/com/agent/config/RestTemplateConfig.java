package com.agent.config;

import lombok.extern.slf4j.Slf4j;
import org.apache.hc.client5.http.config.RequestConfig;
import org.apache.hc.client5.http.impl.classic.CloseableHttpClient;
import org.apache.hc.client5.http.impl.classic.HttpClients;
import org.apache.hc.client5.http.impl.io.PoolingHttpClientConnectionManager;
import org.apache.hc.client5.http.impl.io.PoolingHttpClientConnectionManagerBuilder;
import org.apache.hc.core5.util.TimeValue;
import org.apache.hc.core5.util.Timeout;
import org.springframework.boot.web.client.RestTemplateBuilder;
import org.springframework.context.annotation.Bean;
import org.springframework.context.annotation.Configuration;
import org.springframework.http.client.HttpComponentsClientHttpRequestFactory;
import org.springframework.http.converter.StringHttpMessageConverter;
import org.springframework.web.client.RestTemplate;

import java.nio.charset.StandardCharsets;

/**
 * 网关 HTTP 客户端配置。
 *
 * <p>使用 Spring 的 {@link RestTemplate} 调用上游 FastAPI，底层换成 Apache HttpClient 5，
 * 从而获得两件 RestTemplate 默认实现（{@code SimpleClientHttpRequestFactory}）给不了的能力：
 *
 * <ol>
 *   <li><b>连接池</b>：{@code PoolingHttpClientConnectionManager} 复用 TCP 连接，
 *       避免每次问答都重新握手；</li>
 *   <li><b>可控超时</b>：连接超时 {@code agent.api.connect-timeout}（默认 10s），
 *       响应超时 {@code agent.api.read-timeout}（默认 180s，覆盖上游最长 90s 的同步问答）。</li>
 * </ol>
 *
 * <p>注意：Spring Framework 6（Spring Boot 3）只支持 Apache HttpClient 5，
 * 因此这里用的是 {@code org.apache.hc.client5.*} 而不是 4.x 的 {@code org.apache.http.*}。
 *
 * <p>另外显式把 {@code StringHttpMessageConverter} 的默认字符集设为 UTF-8，
 * 避免中文分析结论出现乱码。
 */
@Slf4j
@Configuration
public class RestTemplateConfig {

    /**
     * 构建带连接池与超时的 RestTemplate。
     *
     * @param builder      Spring Boot 提供的 RestTemplateBuilder
     * @param properties   上游连接配置
     * @return RestTemplate 实例
     */
    @Bean
    public RestTemplate agentRestTemplate(RestTemplateBuilder builder, AgentApiProperties properties) {

        // ---- 连接池 --------------------------------------------------
        PoolingHttpClientConnectionManager connectionManager =
                PoolingHttpClientConnectionManagerBuilder.create()
                        .setMaxConnTotal(properties.getMaxTotalConnections())
                        .setMaxConnPerRoute(properties.getMaxConnectionsPerRoute())
                        .build();
        connectionManager.setValidateAfterInactivity(
                TimeValue.ofMilliseconds(properties.getValidateAfterInactivityMillis()));

        // ---- 超时 ----------------------------------------------------
        // HttpClient 5 的 RequestConfig 全部使用 Timeout（毫秒精度）
        RequestConfig requestConfig = RequestConfig.custom()
                .setConnectTimeout(Timeout.ofMilliseconds(properties.getConnectTimeout()))
                .setConnectionRequestTimeout(
                        Timeout.ofMilliseconds(properties.getConnectionRequestTimeout()))
                .setResponseTimeout(Timeout.ofMilliseconds(properties.getReadTimeout()))
                .build();

        CloseableHttpClient httpClient = HttpClients.custom()
                .setConnectionManager(connectionManager)
                .setDefaultRequestConfig(requestConfig)
                .evictIdleConnections(TimeValue.ofSeconds(30))
                .evictExpiredConnections()
                // 不自动重试：问答接口非幂等，重试可能造成重复执行
                .disableAutomaticRetries()
                .build();

        // 超时与连接池全部在 HttpClient 侧配置好；
        // 这里不再重复 setConnectTimeout/setReadTimeout，避免两处配置互相覆盖。
        HttpComponentsClientHttpRequestFactory requestFactory =
                new HttpComponentsClientHttpRequestFactory(httpClient);

        RestTemplate restTemplate = builder
                .requestFactory(() -> requestFactory)
                // 出站请求体日志：排查「上游 422」这类契约不一致问题时，能直接看到
                // 网关实际发出的 JSON（DEBUG 级别，生产可关闭）
                .additionalInterceptors((request, body, execution) -> {
                    if (log.isDebugEnabled() && body != null && body.length > 0) {
                        log.debug("上游请求体 {} {} -> {}",
                                request.getMethod(), request.getURI(),
                                new String(body, StandardCharsets.UTF_8));
                    }
                    return execution.execute(request, body);
                })
                .build();

        // 中文不乱码：显式指定 UTF-8
        restTemplate.getMessageConverters().stream()
                .filter(converter -> converter instanceof StringHttpMessageConverter)
                .map(converter -> (StringHttpMessageConverter) converter)
                .forEach(converter -> converter.setDefaultCharset(StandardCharsets.UTF_8));

        log.info("Agent 上游 RestTemplate 已初始化：baseUrl={}，连接超时={}ms，读取超时={}ms，"
                        + "连接池 maxTotal={} maxPerRoute={}",
                properties.getBaseUrl(),
                properties.getConnectTimeout(),
                properties.getReadTimeout(),
                properties.getMaxTotalConnections(),
                properties.getMaxConnectionsPerRoute());

        return restTemplate;
    }
}
