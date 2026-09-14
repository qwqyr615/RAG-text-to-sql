package com.agent;

import lombok.extern.slf4j.Slf4j;
import org.springframework.boot.SpringApplication;
import org.springframework.boot.autoconfigure.SpringBootApplication;
import org.springframework.boot.context.properties.EnableConfigurationProperties;
import org.springframework.core.env.Environment;

import com.agent.config.AgentApiProperties;

/**
 * Java 网关启动类。
 *
 * <p>企业数据底座智能问析 Agent 系统的后端入口：对前端（React）暴露统一 REST 契约，
 * 向下调用 Python Agent 内核的 FastAPI 适配层（默认 {@code http://127.0.0.1:8000}）。
 *
 * <p>启动前提：<b>必须先启动 FastAPI 上游</b>，否则网关可以正常启动，
 * 但所有代理接口都会返回 {@code code=0} 与「Agent 服务不可用」提示。
 */
@Slf4j
@SpringBootApplication
@EnableConfigurationProperties(AgentApiProperties.class)
public class AgentGatewayApplication {

    public static void main(String[] args) {
        Environment env = SpringApplication.run(AgentGatewayApplication.class, args).getEnvironment();
        String port = env.getProperty("server.port", "8080");
        String contextPath = env.getProperty("server.servlet.context-path", "");
        log.info("""
                
                ==========================================================
                  企业数据底座智能问析 Agent 系统 - Java 网关 启动成功
                ----------------------------------------------------------
                  本地地址   : http://127.0.0.1:{}{}
                  ActiveProfile: {}
                  上游 Agent : {}
                ==========================================================
                """,
                port,
                contextPath,
                String.join(",", env.getActiveProfiles()),
                env.getProperty("agent.api.base-url", "http://127.0.0.1:8000"));
    }
}
