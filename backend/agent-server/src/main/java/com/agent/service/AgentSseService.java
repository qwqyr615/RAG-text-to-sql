package com.agent.service;

import com.agent.constant.AgentConstants;
import com.agent.util.JsonUtils;
import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import lombok.extern.slf4j.Slf4j;
import org.springframework.core.io.Resource;
import org.springframework.http.ResponseEntity;
import org.springframework.stereotype.Service;
import org.springframework.web.servlet.mvc.method.annotation.SseEmitter;

import java.io.ByteArrayOutputStream;
import java.io.IOException;
import java.io.InputStream;
import java.nio.charset.StandardCharsets;
import java.util.AbstractMap;
import java.util.Map;

/**
 * SSE 流代理服务。
 *
 * <p>把上游 FastAPI 的 {@code GET /api/v1/agent/jobs/{job_id}/stream}
 * 转成 Spring MVC 的 {@link SseEmitter}，实现<b>逐帧透传</b>：
 * 上游推一帧，网关立刻推一帧，中间不做整体缓冲，前端因此能看到实时进度。
 *
 * <h3>为什么不用 RestTemplate 的流式回调</h3>
 * {@code RestTemplate.execute(..., ResponseExtractor)} 是同步阻塞的，
 * 直接在里面 {@code emitter.send} 会占用 Servlet 请求线程直到流结束（最长 10 分钟），
 * 在高并发下会耗尽 Tomcat 线程池。因此这里<b>只借用 RestTemplate 的
 * 连接池与超时配置</b>去打开连接，真正的读取与转发交给一个独立线程完成。
 *
 * <h3>协议对齐</h3>
 * 上游事件的原始形态是：
 * <pre>
 * event: progress
 * data: {"job_id":"...","status":"running","progress":20,...}
 * </pre>
 * 网关解析出事件名与 data 原文后原样重发，保证前端 {@code EventSource}
 * 或 fetch-stream 的解析逻辑不需要任何改动。
 */
@Slf4j
@Service
public class AgentSseService {

    /** SSE 连接最长存活时间：与上游一致，10 分钟，避免连接泄漏 */
    private static final long SSE_TIMEOUT_MILLIS = 600_000L;

    /** HTTP 文本类型（SSE 的 data 行原文下发，需要显式指定为 text/plain） */
    private static final org.springframework.http.MediaType TEXT_PLAIN =
            org.springframework.http.MediaType.TEXT_PLAIN;

    private final AgentApiClient agentApiClient;
    private final ObjectMapper objectMapper = JsonUtils.mapper();

    public AgentSseService(AgentApiClient agentApiClient) {
        this.agentApiClient = agentApiClient;
    }

    /**
     * 打开对上游任务进度流的代理，并返回给浏览器的 {@link SseEmitter}。
     *
     * @param jobId 任务 ID
     * @return SseEmitter，由 Spring MVC 负责把事件写给浏览器
     */
    public SseEmitter streamJob(String jobId) {
        SseEmitter emitter = new SseEmitter(SSE_TIMEOUT_MILLIS);

        Thread worker = new Thread(() -> relay(jobId, emitter), "agent-sse-" + jobId);
        worker.setDaemon(true);
        worker.start();

        return emitter;
    }

    /**
     * 独立线程中的转发逻辑：打开上游流 -> 逐帧解析 -> 逐帧下发。
     *
     * @param jobId   任务 ID
     * @param emitter 前端 SSE 端点
     */
    private void relay(String jobId, SseEmitter emitter) {
        ResponseEntity<Resource> response;
        try {
            // 这一步会阻塞直到上游返回响应头（上游会立即返回 event-stream 响应头）
            response = agentApiClient.openJobStream(jobId);
        } catch (Exception e) {
            log.warn("打开上游 SSE 失败 jobId={}：{}", jobId, e.getMessage());
            sendErrorAndComplete(emitter, e.getMessage());
            return;
        }

        Resource body = response.getBody();
        if (body == null) {
            sendErrorAndComplete(emitter, AgentConstants.MSG_BAD_UPSTREAM_RESPONSE());
            return;
        }

        log.debug("上游 SSE 已连接 jobId={}，HTTP {}", jobId, response.getStatusCode().value());

        try (InputStream in = body.getInputStream()) {
            ByteArrayOutputStream pending = new ByteArrayOutputStream(4096);
            byte[] chunk = new byte[1024];
            int read;

            while ((read = in.read(chunk)) != -1) {
                pending.write(chunk, 0, read);

                // 一次 read 可能拿到多帧：先整体解码，再逐帧切分，
                // 避免「字节边界截断 UTF-8 汉字」导致乱码。
                String text = decodeAndClear(pending);
                int cursor = 0;
                int separator;
                boolean finished = false;

                while ((separator = indexOfBlankLine(text, cursor)) >= 0) {
                    String rawEvent = text.substring(cursor, separator);
                    cursor = separator + blankLineLength(text, separator);

                    Map.Entry<String, String> frame = parseFrame(rawEvent);
                    emitter.send(SseEmitter.event().name(frame.getKey()).data(frame.getValue(), TEXT_PLAIN));

                    if (AgentConstants.SSE_EVENT_DONE.equals(frame.getKey())) {
                        log.debug("上游任务已结束，关闭 SSE jobId={}", jobId);
                        emitter.complete();
                        finished = true;
                        break;
                    }
                }
                if (finished) {
                    return;
                }

                // 尚未成帧的尾部字节写回缓冲区，等待下一批数据
                writeBack(pending, text.substring(cursor));
            }

            // 上游主动断流：把残留数据发完再结束，避免前端卡在等待状态
            String tail = decodeAndClear(pending).trim();
            if (!tail.isEmpty()) {
                emitter.send(SseEmitter.event()
                        .name(AgentConstants.SSE_EVENT_PROGRESS)
                        .data(tail, TEXT_PLAIN));
            }
            emitter.complete();

        } catch (IOException e) {
            // 前端关闭页面会走到这里（Broken pipe），属正常情况，不打 error 日志
            log.debug("SSE 连接已中断 jobId={}：{}", jobId, e.getMessage());
            emitter.completeWithError(e);
        } catch (Exception e) {
            log.warn("SSE 转发异常 jobId={}", jobId, e);
            sendErrorAndComplete(emitter, e.getMessage());
        }
    }

    /**
     * 解析单个 SSE 事件块（不含末尾空行）。
     *
     * <p>只关心 {@code event:} 与 {@code data:} 两类字段，
     * {@code id:} / {@code retry:} / 注释行按 SSE 规范忽略。
     *
     * @param rawEvent 事件块原文
     * @return 事件（event 名 -> data 原文）
     */
    private Map.Entry<String, String> parseFrame(String rawEvent) {
        String eventName = AgentConstants.SSE_EVENT_PROGRESS;
        StringBuilder data = new StringBuilder();

        for (String line : rawEvent.split("\r?\n")) {
            if (line.isEmpty()) {
                continue;
            }
            if (line.startsWith("event:")) {
                eventName = line.substring("event:".length()).trim();
            } else if (line.startsWith("data:")) {
                if (data.length() > 0) {
                    data.append('\n');
                }
                data.append(line.substring("data:".length()).trim());
            }
        }
        return new AbstractMap.SimpleEntry<>(eventName, data.toString());
    }

    /**
     * 把字节缓冲区按 UTF-8 解码成字符串并清空缓冲区。
     *
     * @param buffer 字节缓冲区
     * @return 解码后的文本
     */
    private String decodeAndClear(ByteArrayOutputStream buffer) {
        String text = new String(buffer.toByteArray(), StandardCharsets.UTF_8);
        buffer.reset();
        return text;
    }

    /**
     * 把尚未处理的文本重新写回缓冲区。
     *
     * @param buffer 字节缓冲区
     * @param text   待写回的文本
     */
    private void writeBack(ByteArrayOutputStream buffer, String text) {
        if (text == null || text.isEmpty()) {
            return;
        }
        byte[] bytes = text.getBytes(StandardCharsets.UTF_8);
        buffer.write(bytes, 0, bytes.length);
    }

    /**
     * 查找空行分隔符位置（兼容 {@code \n\n} 与 {@code \r\n\r\n}）。
     *
     * @param text  文本
     * @param from  起始查找下标
     * @return 分隔符起始下标，找不到返回 -1
     */
    private int indexOfBlankLine(String text, int from) {
        int lf = text.indexOf("\n\n", from);
        int crlf = text.indexOf("\r\n\r\n", from);
        if (lf < 0) {
            return crlf;
        }
        if (crlf < 0) {
            return lf;
        }
        return Math.min(lf, crlf);
    }

    /**
     * 计算空行分隔符的长度。
     *
     * @param text      文本
     * @param separator 分隔符起始下标
     * @return 分隔符字节长度
     */
    private int blankLineLength(String text, int separator) {
        return text.startsWith("\r\n\r\n", separator) ? 4 : 2;
    }

    /**
     * 向上游流打开失败时，用一次 {@code error} 事件把原因告诉前端。
     *
     * <p>这里刻意不直接 {@code completeWithError}：那样浏览器只能看到一个断开的
     * 连接，拿不到可读原因。发一个 error 事件前端就能提示「Agent 服务不可用」。
     *
     * @param emitter 前端 SSE 端点
     * @param message 错误信息
     */
    private void sendErrorAndComplete(SseEmitter emitter, String message) {
        String reason = message == null || message.trim().isEmpty()
                ? AgentConstants.MSG_SERVICE_UNAVAILABLE()
                : message;
        try {
            JsonNode payload = objectMapper.createObjectNode().put("error", reason);
            emitter.send(SseEmitter.event().name("error").data(payload.toString(),
                    org.springframework.http.MediaType.APPLICATION_JSON));
            emitter.complete();
        } catch (Exception ignored) {
            // 前端已经断开，无需再处理
            emitter.completeWithError(new IOException("SSE 连接已被前端关闭"));
        }
    }
}
