package com.agent.controller;

import com.agent.dto.AnomalyRequestDTO;
import com.agent.dto.RegressionRequestDTO;
import com.agent.dto.TrainRequestDTO;
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

    /**
     * 列出支持的建模算法及元信息。
     *
     * <p>前端据此渲染算法选择卡片与参数表单；新增算法只需在上游
     * {@code ALGORITHM_CATALOG} 加一条，网关无需改动。
     *
     * @return {@code {algorithms:[{name,label,family,requires_target,params,...}], total, table}}
     */
    @GetMapping("/algorithms")
    public Result<Map<String, Object>> algorithms() {
        return agentApiClient.algorithms();
    }

    /**
     * 统一建模入口：决策树 / 随机森林 / 逻辑回归 / KMeans。
     *
     * <p>与 {@code /anomaly}、{@code /regression} 的关系：后两者是早期为便于调试
     * 单独开放的专用接口，本接口是统一入口，最终都调用上游同一批建模函数，
     * 因此结果结构一致。
     *
     * @param request 建模请求
     * @return 建模结果信封（字段随算法不同，用 Map 承接）
     */
    @PostMapping("/train")
    public Result<Map<String, Object>> train(@RequestBody TrainRequestDTO request) {
        if (request == null) {
            throw new BaseException("请求体不能为空");
        }
        if (request.getAlgorithm() == null || request.getAlgorithm().trim().isEmpty()) {
            throw new BaseException("算法名 algorithm 不能为空");
        }
        // 只拦截明显越界的值；算法与必填参数的组合校验由上游按算法分派完成，
        // 避免在网关重复实现一套规则导致两边不一致
        if (request.getTestSize() != null
                && (request.getTestSize() <= 0 || request.getTestSize() >= 1)) {
            throw new BaseException("testSize 必须介于 0 与 1 之间（不含边界）");
        }
        if (request.getMaxDepth() != null
                && (request.getMaxDepth() < 1 || request.getMaxDepth() > 50)) {
            throw new BaseException("maxDepth 必须介于 1 与 50 之间");
        }
        if (request.getNEstimators() != null
                && (request.getNEstimators() < 1 || request.getNEstimators() > 500)) {
            throw new BaseException("nEstimators 必须介于 1 与 500 之间");
        }
        if (request.getNClusters() != null
                && (request.getNClusters() < 2 || request.getNClusters() > 20)) {
            throw new BaseException("nClusters 必须介于 2 与 20 之间");
        }
        log.info("统一建模：algorithm={}，target={}，features={}，limit={}",
                request.getAlgorithm(), request.getTarget(),
                request.getFeatures() == null ? null : request.getFeatures().size(),
                request.getLimit());
        return agentApiClient.train(request);
    }
}
