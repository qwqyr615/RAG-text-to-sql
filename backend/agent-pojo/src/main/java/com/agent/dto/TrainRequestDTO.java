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
import java.util.List;

/**
 * 统一建模请求 DTO。
 *
 * <p>对应上游 {@code POST /api/v1/modeling/train}（决策树 / 随机森林 /
 * 逻辑回归 / KMeans 走这一个入口）。
 *
 * <h3>为什么用一个通用 DTO 而不是每个算法一个</h3>
 * 算法参数差异大（{@code maxDepth} / {@code nEstimators} / {@code threshold} /
 * {@code nClusters}），若为每个算法单独建 DTO 与接口，网关和前端都要跟着改。
 * 这里统一入口，参数校验由上游建模层按算法分派完成。
 *
 * <h3>命名策略（与 AgentAskDTO / RegressionRequestDTO 保持一致）</h3>
 * 全局 Jackson 策略是 {@code SNAKE_CASE}，会把 {@code testSize} 写成
 * {@code test_size}。但上游 {@code TrainRequest} 对这些字段声明了
 * <b>camelCase 显式别名</b>，而<b>显式别名的优先级高于字段名</b>。
 *
 * <p>实测踩过的坑：不加 {@link JsonProperty} 时，网关发出的
 * {@code test_size: 1.5} 与上游别名 {@code testSize} 不匹配，pydantic 既没报错
 * 也没采用该值，而是<b>静默回落到默认值</b>——表现为 {@code maxDepth=99} 被忽略、
 * 实际用了 {@code max_depth=5}，且 HTTP 返回 200。这类「参数被悄悄丢掉」的
 * 故障最难排查，因此这里对每个带别名的字段都显式声明 camelCase 主名，
 * 并用 {@link JsonAlias} 兼容 snake_case 入参。
 *
 * <h3>为什么必须 NON_NULL</h3>
 * 上游把这些字段声明为可选，传 {@code null} 会被 pydantic 判为类型不合法
 * （HTTP 422），因此让 null 字段不出现在请求体里，由上游落到默认值。
 */
@Data
@Builder
@NoArgsConstructor
@AllArgsConstructor
@JsonIgnoreProperties(ignoreUnknown = true)
@JsonInclude(JsonInclude.Include.NON_NULL)
public class TrainRequestDTO implements Serializable {

    private static final long serialVersionUID = 1L;

    /** 算法名：decision_tree / random_forest / logistic_regression / kmeans */
    private String algorithm;

    /** 目标列（有监督算法必填；KMeans 不使用） */
    private String target;

    /** 特征列；为空时由上游使用默认特征集 */
    private List<String> features;

    /** 最多读取行数 */
    private Integer limit;

    /** 测试集比例，上游约束 (0, 1) 开区间 */
    @JsonProperty("testSize")
    @JsonAlias({"test_size", "testsize"})
    private Double testSize;

    /** 树最大深度（决策树 / 随机森林），上游约束 1~50 */
    @JsonProperty("maxDepth")
    @JsonAlias({"max_depth", "maxdepth"})
    private Integer maxDepth;

    /** 随机森林树数量，上游约束 1~500 */
    @JsonProperty("nEstimators")
    @JsonAlias({"n_estimators", "nestimators"})
    private Integer nEstimators;

    /** 强制任务类型：classification / regression；为空则由上游按目标列自动判定 */
    @JsonProperty("taskType")
    @JsonAlias({"task_type", "tasktype"})
    private String taskType;

    /** 逻辑回归二分阈值；为空时上游取目标列中位数 */
    private Double threshold;

    /** KMeans 聚类数，上游约束 2~20；为空时上游按轮廓系数自动选择 */
    @JsonProperty("nClusters")
    @JsonAlias({"n_clusters", "nclusters"})
    private Integer nClusters;

    /** KMeans 自动选 k 的上限 */
    @JsonProperty("maxK")
    @JsonAlias({"max_k", "maxk"})
    private Integer maxK;
}
