package com.agent.dto;

import com.fasterxml.jackson.annotation.JsonAlias;
import com.fasterxml.jackson.annotation.JsonIgnoreProperties;
import com.fasterxml.jackson.annotation.JsonInclude;
import com.fasterxml.jackson.annotation.JsonProperty;
import lombok.AllArgsConstructor;
import lombok.Builder;
import lombok.Data;
import lombok.NoArgsConstructor;

import java.io.Serializable;

/**
 * 自然语言问答请求 DTO。
 *
 * <p>对应上游 {@code POST /api/v1/agent/ask}、{@code /agent/report}、
 * {@code /agent/ask/async} 的请求体。
 *
 * <h3>命名策略说明</h3>
 * 全局 Jackson 策略是 {@code SNAKE_CASE}（见 {@code application.yml}），
 * 因此不加注解的字段在网络上都是 snake_case（{@code question}、{@code metadata}）。
 * 但上游 pydantic 模型 {@code AskRequest} 对这三个字段声明了显式 alias，
 * <b>显式 alias 的优先级高于字段名</b>，所以必须用 {@link JsonProperty}
 * 强制写出 camelCase，并配合 {@link JsonAlias} 同时兼容 camelCase / snake_case 入参。
 *
 * <p>这样网关对上游的请求体是「两种写法都认」的最稳形态。
 *
 * <h3>为什么必须 NON_NULL</h3>
 * 上游 pydantic 模型把 {@code metadata} / {@code business_rules} 声明为
 * 非 Optional 的字符串（默认 {@code ""}）。若网关把它们序列化成
 * {@code "metadata": null}，pydantic 会判定类型不合法并返回 <b>HTTP 422</b>；
 * 而<b>缺省该字段</b>则能正确落到默认值。因此这里用
 * {@link JsonInclude.Include#NON_NULL} 让 null 字段直接不出现在请求体里。
 */
@Data
@Builder
@NoArgsConstructor
@AllArgsConstructor
@JsonIgnoreProperties(ignoreUnknown = true)
@JsonInclude(JsonInclude.Include.NON_NULL)
public class AgentAskDTO implements Serializable {

    private static final long serialVersionUID = 1L;

    /** 用户自然语言问题（必填） */
    private String question;

    /** 会话 ID，用于多轮上下文；缺省为 default */
    @JsonProperty("sessionId")
    @JsonAlias({"session_id", "sessionid"})
    private String sessionId;

    /** 外部补充的数据资源说明 */
    private String metadata;

    /** 外部补充的业务口径 */
    @JsonProperty("businessRules")
    @JsonAlias({"business_rules", "businessrules"})
    private String businessRules;

    /** 是否生成图表配置 */
    @JsonProperty("wantChart")
    @JsonAlias({"want_chart", "wantchart"})
    private Boolean wantChart;

    /** 是否生成 Markdown 报告 */
    @JsonProperty("wantReport")
    @JsonAlias({"want_report", "wantreport"})
    private Boolean wantReport;
}
