package com.agent.controller;

import com.agent.constant.AgentConstants;
import com.agent.dto.AgentAskDTO;
import com.agent.exception.BaseException;
import com.agent.result.Result;
import com.agent.service.AgentApiClient;
import com.agent.service.AgentSseService;
import com.agent.vo.AskResponseVO;
import com.agent.vo.JobStateVO;
import com.agent.vo.JobSubmitVO;
import com.agent.vo.SessionResetVO;
import lombok.extern.slf4j.Slf4j;
import org.springframework.http.MediaType;
import org.springframework.web.bind.annotation.DeleteMapping;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PathVariable;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RestController;
import org.springframework.web.servlet.mvc.method.annotation.SseEmitter;

/**
 * 智能分析接口：同步问答 / 报告 / 异步任务 / SSE 进度流 / 会话重置。
 *
 * <p>对应上游 {@code /api/v1/agent/*}。Controller 只做参数校验与转发，
 * 所有 HTTP 细节都在 {@link AgentApiClient} 与 {@link AgentSseService}。
 */
@Slf4j
@RestController
@RequestMapping("/api/v1/agent")
public class AgentController {

    private final AgentApiClient agentApiClient;
    private final AgentSseService agentSseService;

    public AgentController(AgentApiClient agentApiClient, AgentSseService agentSseService) {
        this.agentApiClient = agentApiClient;
        this.agentSseService = agentSseService;
    }

    /**
     * 自然语言问答（同步）。
     *
     * <p><b>注意</b>：上游最长会阻塞 90 秒，网关读取超时为 180 秒。
     * 大屏等对实时性敏感的场景建议改用 {@code POST /ask/async} + SSE。
     *
     * @param request 问答请求
     * @return {@code {code, msg, data:AskResponse}}
     */
    @PostMapping("/ask")
    public Result<AskResponseVO> ask(@RequestBody AgentAskDTO request) {
        normalize(request);
        log.info("同步问答：sessionId={}，question={}", request.getSessionId(), abbreviate(request.getQuestion()));
        return agentApiClient.ask(request);
    }

    /**
     * 生成 Markdown 分析报告。
     *
     * @param request 问答请求（网关会强制 wantReport=true）
     * @return {@code {code, msg, data:AskResponse}}，data.report 为 Markdown 文本
     */
    @PostMapping("/report")
    public Result<AskResponseVO> report(@RequestBody AgentAskDTO request) {
        normalize(request);
        request.setWantReport(Boolean.TRUE);
        log.info("生成报告：sessionId={}，question={}", request.getSessionId(), abbreviate(request.getQuestion()));
        return agentApiClient.report(request);
    }

    /**
     * 提交异步问答任务（立即返回，适合长耗时分析）。
     *
     * @param request 问答请求
     * @return {@code {code, msg, data:{job_id, status}}}
     */
    @PostMapping("/ask/async")
    public Result<JobSubmitVO> askAsync(@RequestBody AgentAskDTO request) {
        normalize(request);
        log.info("提交异步任务：sessionId={}，question={}", request.getSessionId(), abbreviate(request.getQuestion()));
        return agentApiClient.askAsync(request);
    }

    /**
     * 查询异步任务状态与结果。
     *
     * @param jobId 任务 ID
     * @return {@code {code, msg, data:{job_id, status, created_at, finished_at,
     *         elapsed_ms, progress, stage, result, error}}}
     */
    @GetMapping("/jobs/{jobId}")
    public Result<JobStateVO> job(@PathVariable String jobId) {
        if (jobId == null || jobId.trim().isEmpty()) {
            throw new BaseException("任务 ID 不能为空");
        }
        return agentApiClient.job(jobId);
    }

    /**
     * 订阅异步任务进度流（SSE）。
     *
     * <p>网关用 {@link SseEmitter} 代理上游的 {@code progress} / {@code done} 事件，
     * 事件名与 data 载荷与上游逐字段一致，前端 {@code EventSource} 无需改动。
     *
     * <p>若上游未启动，网关会先发一个 {@code error} 事件（含可读中文原因）
     * 再关闭连接，前端因此不会「静默卡住」。
     *
     * @param jobId 任务 ID
     * @return SSE 端点
     */
    @GetMapping(value = "/jobs/{jobId}/stream", produces = MediaType.TEXT_EVENT_STREAM_VALUE)
    public SseEmitter stream(@PathVariable String jobId) {
        if (jobId == null || jobId.trim().isEmpty()) {
            throw new BaseException("任务 ID 不能为空");
        }
        log.info("订阅任务进度流：jobId={}", jobId);
        return agentSseService.streamJob(jobId);
    }

    /**
     * 清空指定会话的多轮历史。
     *
     * @param sessionId 会话 ID
     * @return {@code {code, msg, data:{session_id, reset}}}
     */
    @DeleteMapping("/session/{sessionId}")
    public Result<SessionResetVO> resetSession(@PathVariable String sessionId) {
        if (sessionId == null || sessionId.trim().isEmpty()) {
            throw new BaseException("会话 ID 不能为空");
        }
        log.info("重置会话：sessionId={}", sessionId);
        return agentApiClient.resetSession(sessionId);
    }

    /**
     * 参数归一化与校验：补默认值，拒绝空问题。
     *
     * <p>下游上游对 {@code question} 有 {@code min_length=1} 约束，
     * 在网关提前拦截可以少一次网络往返，并给出中文提示。
     *
     * @param request 问答请求
     */
    private void normalize(AgentAskDTO request) {
        if (request == null || request.getQuestion() == null || request.getQuestion().trim().isEmpty()) {
            throw new BaseException("问题内容不能为空");
        }
        if (request.getSessionId() == null || request.getSessionId().trim().isEmpty()) {
            request.setSessionId(AgentConstants.DEFAULT_SESSION_ID);
        }
        if (request.getWantChart() == null) {
            request.setWantChart(Boolean.TRUE);
        }
        if (request.getWantReport() == null) {
            request.setWantReport(Boolean.FALSE);
        }
    }

    /**
     * 日志里截断超长问题文本。
     *
     * @param text 原始文本
     * @return 截断后的文本
     */
    private String abbreviate(String text) {
        if (text == null) {
            return "";
        }
        return text.length() <= 60 ? text : text.substring(0, 60) + "...";
    }
}
