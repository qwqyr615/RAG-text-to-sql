package com.agent.config;

import org.springframework.context.annotation.Configuration;
import org.springframework.web.servlet.config.annotation.CorsRegistry;
import org.springframework.web.servlet.config.annotation.WebMvcConfigurer;

/**
 * 跨域（CORS）配置。
 *
 * <p>前端为 Vite 开发服务器，默认跑在 5173 端口。这里显式放行
 * {@code http://localhost:5173} 与 {@code http://127.0.0.1:5173}。
 *
 * <p><b>注意</b>：{@code allowCredentials(true)} 与 {@code allowedOrigins("*")}
 * 不能同时使用（浏览器会拒绝带凭证的通配符响应），因此这里用
 * {@code allowedOriginPatterns} 精确列出开发源。
 */
@Configuration
public class WebMvcConfig implements WebMvcConfigurer {

    /** Vite 开发服务器可能出现的两种 host 写法 */
    private static final String[] ALLOWED_ORIGINS = {
            "http://localhost:5173",
            "http://127.0.0.1:5173"
    };

    @Override
    public void addCorsMappings(CorsRegistry registry) {
        registry.addMapping("/**")
                .allowedOriginPatterns(ALLOWED_ORIGINS)
                .allowedMethods("GET", "POST", "PUT", "DELETE", "PATCH", "OPTIONS", "HEAD")
                .allowedHeaders("*")
                .exposedHeaders("Content-Disposition")
                .allowCredentials(true)
                .maxAge(3600);
    }
}
