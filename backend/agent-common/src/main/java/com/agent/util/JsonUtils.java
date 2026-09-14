package com.agent.util;

import com.fasterxml.jackson.databind.DeserializationFeature;
import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import com.fasterxml.jackson.databind.PropertyNamingStrategies;

/**
 * JSON 工具类。
 *
 * <p>网关大量以 {@code Map}/{@code JsonNode} 承接上游结构不固定的字段
 * （{@code chart_config.option}、{@code rows}、{@code prompt_usage} 等），
 * 这里统一持有一个线程安全的 {@link ObjectMapper}。
 *
 * <h3>为什么这里显式配置 SNAKE_CASE</h3>
 * 上游 FastAPI 的响应统一是 snake_case（{@code table_name}、{@code row_count}、
 * {@code chart_config}、{@code session_id}...）。
 * 虽然 {@code application.yml} 里也配了 {@code spring.jackson.property-naming-strategy}，
 * 但那只作用于 Spring MVC 的入站/出站转换器，<b>不能保证 RestTemplate 内部
 * 使用的转换器就是同一个实例</b>。
 *
 * <p>实测发现 RestTemplate 的消息转换器列表里存在两个 JSON 转换器，
 * 其中一个是 {@code strategy=null}（未套用命名策略），且会先匹配到响应类型，
 * 结果 {@code agent_ready}、{@code table_count} 这类下划线字段全部读成 null。
 *
 * <p>因此网关<b>不依赖转换器的选择顺序</b>：统一先用 {@code String} 接收上游原始
 * JSON，再用本类的 ObjectMapper 自己反序列化。命名策略在代码里显式固定，
 * 无论 Spring 的转换器如何变化都不会丢字段。
 */
public final class JsonUtils {

    private static final ObjectMapper MAPPER = new ObjectMapper()
            // 上游契约：响应字段一律 snake_case → Java 驼峰
            .setPropertyNamingStrategy(PropertyNamingStrategies.SNAKE_CASE)
            // 上游新增字段不应该让网关反序列化失败
            .configure(DeserializationFeature.FAIL_ON_UNKNOWN_PROPERTIES, false);

    private JsonUtils() {
    }

    /**
     * 获取共享的 ObjectMapper（线程安全，可放心复用）。
     *
     * @return ObjectMapper 实例
     */
    public static ObjectMapper mapper() {
        return MAPPER;
    }

    /**
     * 把任意对象序列化成 JSON 字符串；失败返回 {@code null} 而不抛异常。
     *
     * @param value 待序列化对象
     * @return JSON 字符串，失败时为 null
     */
    public static String toJson(Object value) {
        if (value == null) {
            return null;
        }
        try {
            return MAPPER.writeValueAsString(value);
        } catch (Exception e) {
            return null;
        }
    }

    /**
     * 解析 JSON 字符串为 {@link JsonNode}；失败返回 {@code null}。
     *
     * @param json JSON 文本
     * @return JsonNode，失败时为 null
     */
    public static JsonNode parse(String json) {
        if (json == null || json.trim().isEmpty()) {
            return null;
        }
        try {
            return MAPPER.readTree(json);
        } catch (Exception e) {
            return null;
        }
    }
}
