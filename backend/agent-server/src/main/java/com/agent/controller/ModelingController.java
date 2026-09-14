package com.agent.controller;

import com.agent.dto.AnomalyRequestDTO;
import com.agent.dto.RegressionRequestDTO;
import com.agent.exception.BaseException;
import com.agent.result.Result;
import com.agent.service.AgentApiClient;
import com.agent.vo.ModelingFeaturesVO;
import lombok.extern.slf4j.Slf4j;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RequestParam;
import org.springframework.web.bind.annotation.RestController;

import java.util.Map;

/**
 * 机器学习建模接口。
 *
 * <p>对应上游 {@code /api/v1/modeling/*}：异常检测、线性回归、可用字段查询。
 *
 * <p>建模结果里 {@code coefficients}、{@code sample_predictions} 等是结构不固定的
 * 对象，统一用 {@code Map<String,Object>} 承接，保证原样透传。
 */
@Slf4j
@RestController
@RequestMapping("/api/v1/modeling")
public class ModelingController {

    private final AgentApiClient agentApiClient;

    public ModelingController(AgentApiClient agentApiClient) {
        this.agentApiClient = agentApiClient;
    }

    /**
     * 异常检测（Isolation Forest）。
     *
     * @param request 检测请求
     * @return {@code {code, msg, data:{task_type, algorithm, total_records,
     *         anomaly_count, anomaly_ratio, feature_columns, records}}}
     */
    @PostMapping("/anomaly")
    public Result<Map<String, Object>> anomaly(@RequestBody AnomalyRequestDTO request) {
        if (request == null) {
            throw new BaseException("请求体不能为空");
        }
        // 只在显式传值且越界时拦截；不传则由上游使用默认值
        if (request.getContamination() != null
                && (request.getContamination() < 0.001 || request.getContamination() > 0.5)) {
            throw new BaseException("contamination 必须介于 0.001 与 0.5 之间");
        }
        log.info("异常检测：contamination={}，limit={}，features={}",
                request.getContamination(), request.getLimit(), request.getFeatures());
        return agentApiClient.anomaly(request);
    }

    /**
     * 线性回归建模与评估。
     *
     * @param request 建模请求
     * @return {@code {code, msg, data:{task_type, algorithm, target, feature_columns,
     *         train_size, test_size, r2_score, rmse, coefficients, sample_predictions}}}
     */
    @PostMapping("/regression")
    public Result<Map<String, Object>> regression(@RequestBody RegressionRequestDTO request) {
        if (request == null) {
            throw new BaseException("请求体不能为空");
        }
        if (request.getTarget() == null || request.getTarget().trim().isEmpty()) {
            throw new BaseException("目标列 target 不能为空");
        }
        if (request.getTestSize() != null
                && (request.getTestSize() <= 0 || request.getTestSize() >= 1)) {
            throw new BaseException("testSize 必须介于 0 与 1 之间（不含边界）");
        }
        log.info("回归建模：target={}，testSize={}，limit={}",
                request.getTarget(), request.getTestSize(), request.getLimit());
        return agentApiClient.regression(request);
    }

    /**
     * 列出可用于建模的字段，供前端下拉选择。
     *
     * @param role 过滤条件：numeric（仅数值型）/ all（或省略表示全部）
     * @return {@code {code, msg, data:{fields:[{table,name,type,description,numeric}], total}}}
     */
    @GetMapping("/features")
    public Result<ModelingFeaturesVO> features(
            @RequestParam(value = "role", required = false) String role) {
        return agentApiClient.features(role);
    }
}
