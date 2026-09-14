/**
 * 机器建模页。
 *
 * 对应题目「建模能力」：支持常见机器学习建模任务（线性回归、决策树、随机森林、
 * 逻辑回归、KMeans、Isolation Forest），包含模型训练、评估、简单推理与结果解释。
 *
 * 架构：算法清单由后端 `/modeling/algorithms` 提供（单一事实来源），
 * 前端据此渲染算法卡片与参数表单，并统一提交到 `/modeling/train`。
 * 这样新增算法只需改后端 `ALGORITHM_CATALOG`，前端无需改动。
 *
 * 「高级模式」保留模型专属的深度交互（异常得分分布、回归散点、系数图等），
 * 「快速模式」用通用参数表单覆盖全部算法。
 */

import { useEffect, useMemo, useState } from 'react'

import { api } from '../api/client'
import { DataTable } from '../components/DataTable'
import { EChart } from '../components/EChart'
import { ErrorBlock, Loading, PageHeader, StatCard } from '../components/StateBlocks'
import { useAsync } from '../hooks/useAsync'
import { colors } from '../styles/tokens'
import type { AlgorithmInfo, AnomalyResult, RegressionResult, TrainResult } from '../api/types'

type Task = 'anomaly' | 'regression' | 'advanced'

/** 高级模式覆盖的算法（有专属可视化），其余走快速模式 */
const ADVANCED_ALGORITHMS = new Set(['isolation_forest', 'linear_regression'])

const TABS: { key: Task; label: string }[] = [
  { key: 'anomaly', label: '异常检测 · Isolation Forest' },
  { key: 'regression', label: '线性回归 · LinearRegression' },
  { key: 'advanced', label: '决策树 / 随机森林 / 逻辑回归 / 聚类' },
]

export function ModelingPage() {
  const features = useAsync(() => api.modelingFeatures('numeric'), [])
  const algorithms = useAsync(() => api.modelingAlgorithms(), [])
  const [task, setTask] = useState<Task>('anomaly')

  const numericFields = features.data?.fields ?? []
  /** 后端已按建模表过滤；仍做一次去重保护 */
  const uniqueNames = useMemo(
    () => Array.from(new Set(numericFields.map((field) => field.name))).sort(),
    [numericFields],
  )

  /** 快速模式可选的算法（排除已有专属面板的两个） */
  const trainableAlgorithms = useMemo(
    () => (algorithms.data?.algorithms ?? []).filter((item) => !ADVANCED_ALGORITHMS.has(item.name)),
    [algorithms.data],
  )

  const loading = features.loading || algorithms.loading
  const error = features.error ?? algorithms.error

  return (
    <>
      <PageHeader
        eyebrow="MACHINE LEARNING MODELING"
        title="机器建模"
        description="支持异常检测、线性回归、决策树、随机森林、逻辑回归与 KMeans 聚类，包含训练、评估与结果解释。"
        actions={
          features.data?.table ? (
            <span className="tag mono" title={features.data.note}>
              建模数据表：{features.data.table}
            </span>
          ) : undefined
        }
      />

      {loading && <Loading text="正在读取可用建模字段与算法…" />}
      {error && (
        <ErrorBlock
          message={error}
          onRetry={() => {
            features.reload()
            algorithms.reload()
          }}
        />
      )}

      {!loading && !error && (
        <>
          <div className="row" style={{ gap: 4, marginBottom: 26, flexWrap: 'wrap' }}>
            {TABS.map((item) => (
              <button
                key={item.key}
                type="button"
                className="btn"
                onClick={() => setTask(item.key)}
                style={{
                  color: task === item.key ? 'var(--c-blue)' : 'var(--c-black)',
                  background: task === item.key ? 'rgba(24, 99, 220, 0.06)' : 'transparent',
                }}
              >
                {item.label}
              </button>
            ))}
          </div>

          {task === 'anomaly' && (
            <AnomalyPanel
              key={`anomaly-${features.data?.default_features?.anomaly?.length ?? 0}`}
              available={uniqueNames}
              defaultFeatures={features.data?.default_features?.anomaly ?? []}
            />
          )}
          {task === 'regression' && (
            <RegressionPanel
              key={`regression-${features.data?.default_features?.regression?.length ?? 0}`}
              available={uniqueNames}
              defaultFeatures={features.data?.default_features?.regression ?? []}
            />
          )}
          {task === 'advanced' && (
            <TrainPanel available={uniqueNames} algorithms={trainableAlgorithms} />
          )}
        </>
      )}
    </>
  )
}

// ---------------------------------------------------------------------------
// 异常检测（模型专属面板）
// ---------------------------------------------------------------------------
function AnomalyPanel({
  available,
  defaultFeatures,
}: {
  available: string[]
  defaultFeatures: string[]
}) {
  const [contamination, setContamination] = useState(0.05)
  const [selected, setSelected] = useState<string[]>(defaultFeatures)
  const [result, setResult] = useState<AnomalyResult | null>(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const run = async () => {
    setLoading(true)
    setError(null)
    try {
      setResult(
        await api.anomaly({
          features: selected.length ? selected : null,
          contamination,
          limit: 10000,
        }),
      )
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : String(caught))
    } finally {
      setLoading(false)
    }
  }

  const chartOption = useMemo(() => {
    if (!result?.records?.length) return null
    const scores = result.records
      .map((record) => Number(record.anomaly_score))
      .filter((value) => Number.isFinite(value))
    if (!scores.length) return null

    const min = Math.min(...scores)
    const max = Math.max(...scores)
    const bucketCount = Math.min(12, Math.max(5, Math.ceil(Math.sqrt(scores.length)) + 3))
    const step = (max - min) / bucketCount || 1
    const labels: string[] = []
    const counts: number[] = new Array(bucketCount).fill(0)
    for (let index = 0; index < bucketCount; index += 1) {
      labels.push((min + step * index).toFixed(3))
    }
    scores.forEach((score) => {
      const index = Math.min(bucketCount - 1, Math.floor((score - min) / step))
      counts[index] += 1
    })

    return {
      tooltip: { trigger: 'axis' },
      grid: { left: 48, right: 24, top: 36, bottom: 48, containLabel: true },
      xAxis: { type: 'category', data: labels, name: '异常得分', nameLocation: 'middle', nameGap: 30 },
      yAxis: { type: 'value', name: '记录数' },
      series: [{ type: 'bar', data: counts, itemStyle: { color: colors.interactionBlue } }],
    }
  }, [result])

  const recordColumns = result?.records?.length ? Object.keys(result.records[0]) : []

  return (
    <>
      <ParameterCard
        title="PARAMETERS"
        hint="预期异常比例直接决定判定为异常的比例，业务上代表「预期有多少比例的数据是异常的」。"
      >
        <SliderField
          label={`预期异常比例：${(contamination * 100).toFixed(1)}%`}
          min={1}
          max={30}
          value={Math.round(contamination * 100)}
          onChange={(value) => setContamination(value / 100)}
        />
        <FeaturePicker
          available={available}
          selected={selected}
          onChange={setSelected}
          label="参与检测的特征（预选了后端默认特征集；清空则用默认）"
        />
        <RunButton loading={loading} idleText="运行异常检测" runningText="检测中…" onClick={run} />
      </ParameterCard>

      {error && <ErrorAlert title="检测失败" message={error} />}
      {loading && <Loading text="正在训练 Isolation Forest 模型…" />}

      {result && !loading && (
        <>
          <div className="grid grid-4" style={{ marginBottom: 28 }}>
            <StatCard label="样本总数" value={result.total_records.toLocaleString('zh-CN')} />
            <StatCard
              label="异常数量"
              value={result.anomaly_count.toLocaleString('zh-CN')}
              hint={`占比 ${(result.anomaly_ratio * 100).toFixed(2)}%`}
            />
            <StatCard label="参与特征数" value={result.feature_columns.length} />
            <StatCard label="算法" value={<span style={{ fontSize: 20 }}>{result.algorithm}</span>} />
          </div>

          <InterpretationCard>
            使用 <span className="mono">{result.algorithm}</span> 对{' '}
            {result.total_records.toLocaleString('zh-CN')} 条生产记录进行无监督异常检测，
            共识别出 <strong>{result.anomaly_count}</strong> 条异常记录（占比{' '}
            {(result.anomaly_ratio * 100).toFixed(2)}%）。参与判定的特征为：
            {result.feature_columns.join('、')}。异常得分越低表示该记录越偏离整体分布，
            建议结合设备、班次与工艺参数进一步定位根因。
          </InterpretationCard>

          {chartOption && (
            <div className="card" style={{ marginBottom: 24 }}>
              <div className="eyebrow" style={{ marginBottom: 12 }}>
                ANOMALY SCORE DISTRIBUTION
              </div>
              <EChart option={chartOption} height={340} />
            </div>
          )}

          {recordColumns.length > 0 && (
            <>
              <div className="eyebrow" style={{ marginBottom: 12 }}>
                异常记录明细（最多 20 条）
              </div>
              <DataTable
                columns={recordColumns}
                rows={result.records.map((record) => recordColumns.map((column) => record[column]))}
              />
            </>
          )}
        </>
      )}
    </>
  )
}

// ---------------------------------------------------------------------------
// 线性回归（模型专属面板）
// ---------------------------------------------------------------------------
function RegressionPanel({
  available,
  defaultFeatures,
}: {
  available: string[]
  defaultFeatures: string[]
}) {
  const [target, setTarget] = useState('defect_rate')
  const [selected, setSelected] = useState<string[]>(defaultFeatures)
  const [result, setResult] = useState<RegressionResult | null>(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const run = async () => {
    setLoading(true)
    setError(null)
    try {
      setResult(
        await api.regression({
          target,
          features: selected.length ? selected : null,
          limit: 10000,
        }),
      )
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : String(caught))
    } finally {
      setLoading(false)
    }
  }

  const coefficientOption = useMemo(() => {
    if (!result?.coefficients) return null
    const entries = Object.entries(result.coefficients)
    if (!entries.length) return null
    entries.sort((a, b) => Math.abs(b[1]) - Math.abs(a[1]))

    return {
      tooltip: { trigger: 'axis', axisPointer: { type: 'shadow' } },
      grid: { left: 140, right: 40, top: 24, bottom: 32, containLabel: false },
      xAxis: { type: 'value', name: '回归系数' },
      yAxis: { type: 'category', data: entries.map(([name]) => name), axisLabel: { fontSize: 11 } },
      series: [
        {
          type: 'bar',
          data: entries.map(([, value]) => ({
            value,
            itemStyle: { color: value >= 0 ? colors.interactionBlue : colors.focusPurple },
          })),
          label: { show: true, position: 'right', fontSize: 11, formatter: '{c}' },
        },
      ],
    }
  }, [result])

  const scatterOption = useMemo(() => {
    if (!result?.sample_predictions?.length) return null
    const points = result.sample_predictions
      .map((record) => [Number(record.actual), Number(record.predict)])
      .filter(([actual, predict]) => Number.isFinite(actual) && Number.isFinite(predict))
    if (!points.length) return null

    const all = points.flat()
    const min = Math.min(...all)
    const max = Math.max(...all)

    return {
      tooltip: {
        trigger: 'item',
        formatter: (params: { data: number[] }) => `实际：${params.data[0]}<br/>预测：${params.data[1]}`,
      },
      grid: { left: 56, right: 32, top: 32, bottom: 48, containLabel: true },
      xAxis: { type: 'value', name: '实际值', min, max },
      yAxis: { type: 'value', name: '预测值', min, max },
      series: [
        {
          type: 'scatter',
          symbolSize: 9,
          data: points,
          itemStyle: { color: colors.interactionBlue, opacity: 0.75 },
        },
        {
          type: 'line',
          data: [
            [min, min],
            [max, max],
          ],
          symbol: 'none',
          lineStyle: { color: colors.mutedSlate, type: 'dashed', width: 1 },
          silent: true,
        },
      ],
    }
  }, [result])

  const predictionColumns = result?.sample_predictions?.length
    ? Object.keys(result.sample_predictions[0])
    : []

  return (
    <>
      <ParameterCard title="PARAMETERS">
        <TargetSelect value={target} onChange={setTarget} available={available} />
        <FeaturePicker
          available={available.filter((name) => name !== target)}
          selected={selected}
          onChange={setSelected}
          label="特征字段（预选了后端默认特征集；清空则用默认，自动排除目标字段）"
        />
        <RunButton loading={loading} idleText="训练回归模型" runningText="训练中…" onClick={run} />
      </ParameterCard>

      {error && <ErrorAlert title="建模失败" message={error} />}
      {loading && <Loading text="正在训练线性回归模型…" />}

      {result && !loading && (
        <>
          <div className="grid grid-4" style={{ marginBottom: 28 }}>
            <StatCard
              label="R² 拟合优度"
              value={result.r2_score.toFixed(4)}
              hint={result.r2_score >= 0.7 ? '拟合较好' : result.r2_score >= 0.4 ? '拟合一般' : '拟合较弱'}
            />
            <StatCard label="RMSE" value={result.rmse.toFixed(4)} hint="均方根误差，越小越好" />
            <StatCard label="训练样本" value={result.train_size.toLocaleString('zh-CN')} />
            <StatCard label="测试样本" value={result.test_size.toLocaleString('zh-CN')} />
          </div>

          <InterpretationCard>
            以 <span className="mono">{result.target}</span> 为目标变量，
            使用 {result.feature_columns.length} 个特征训练 {result.algorithm}。
            R² = <strong>{result.r2_score.toFixed(4)}</strong> 表示模型可解释目标变量约{' '}
            <strong>{(result.r2_score * 100).toFixed(1)}%</strong> 的变异；
            RMSE = {result.rmse.toFixed(4)} 表示预测值与真实值的平均偏离幅度。
            {result.r2_score < 0.4 &&
              '当前拟合较弱，说明目标变量与所选特征之间线性关系不明显，建议更换特征组合或改用非线性模型。'}
            下方系数图中数值为正表示该特征增大时目标值上升，为负则相反。
          </InterpretationCard>

          <div className="grid grid-2" style={{ marginBottom: 24 }}>
            {coefficientOption && (
              <div className="card">
                <div className="eyebrow" style={{ marginBottom: 12 }}>
                  特征回归系数
                </div>
                <EChart
                  option={coefficientOption}
                  height={Math.max(280, Object.keys(result.coefficients).length * 34)}
                />
              </div>
            )}
            {scatterOption && (
              <div className="card">
                <div className="eyebrow" style={{ marginBottom: 12 }}>
                  预测值 vs 实际值（虚线为理想拟合）
                </div>
                <EChart
                  option={scatterOption}
                  height={Math.max(280, Object.keys(result.coefficients).length * 34)}
                />
              </div>
            )}
          </div>

          {predictionColumns.length > 0 && (
            <>
              <div className="eyebrow" style={{ marginBottom: 12 }}>
                预测对比样例（测试集前 10 条）
              </div>
              <DataTable
                columns={predictionColumns}
                rows={result.sample_predictions.map((record) =>
                  predictionColumns.map((column) => record[column]),
                )}
              />
            </>
          )}
        </>
      )}
    </>
  )
}

// ---------------------------------------------------------------------------
// 统一建模面板：决策树 / 随机森林 / 逻辑回归 / KMeans
// ---------------------------------------------------------------------------
function TrainPanel({
  available,
  algorithms,
}: {
  available: string[]
  algorithms: AlgorithmInfo[]
}) {
  const [algorithm, setAlgorithm] = useState('decision_tree')
  const [result, setResult] = useState<TrainResult | null>(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [elapsed, setElapsed] = useState(0)

  // 通用参数
  const [target, setTarget] = useState('defect_rate')
  const [selected, setSelected] = useState<string[]>([])
  const [featuresTouched, setFeaturesTouched] = useState(false)
  const [maxDepth, setMaxDepth] = useState(5)
  const [nEstimators, setNEstimators] = useState(100)
  const [threshold, setThreshold] = useState<string>('')
  const [nClusters, setNClusters] = useState<string>('')

  const current = algorithms.find((item) => item.name === algorithm) ?? algorithms[0]

  // 切换算法时清空上一次结果，避免结果与表单不匹配造成误读
  useEffect(() => {
    setResult(null)
    setError(null)
  }, [algorithm])

  // 预选特征：沿用后端默认特征集（与异常检测一致），用户改动后不再覆盖
  const features = useAsync(() => api.modelingFeatures('numeric'), [])
  useEffect(() => {
    const defaults = features.data?.default_features?.regression ?? []
    if (!featuresTouched && defaults.length) setSelected(defaults)
  }, [features.data, featuresTouched])

  const run = async () => {
    if (!current) return
    setLoading(true)
    setError(null)
    setResult(null)
    setElapsed(0)
    const started = Date.now()
    const timer = window.setInterval(() => setElapsed(Math.floor((Date.now() - started) / 1000)), 1000)

    try {
      const payload = {
        algorithm: current.name,
        target: current.requires_target ? target : null,
        features: selected.length ? selected : null,
        limit: 10000,
        maxDepth: current.params.includes('maxDepth') ? maxDepth : null,
        nEstimators: current.params.includes('nEstimators') ? nEstimators : null,
        threshold:
          current.params.includes('threshold') && threshold.trim() !== ''
            ? Number(threshold)
            : null,
        nClusters:
          current.params.includes('nClusters') && nClusters.trim() !== ''
            ? Number(nClusters)
            : null,
      }
      setResult(await api.train(payload))
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : String(caught))
    } finally {
      window.clearInterval(timer)
      setLoading(false)
    }
  }

  if (!current) {
    return <div className="alert alert-info">后端未返回可用算法清单，请检查建模模块是否正常。</div>
  }

  return (
    <>
      {/* 算法选择卡片 */}
      <div className="grid grid-2" style={{ marginBottom: 24 }}>
        {algorithms.map((item) => {
          const active = item.name === algorithm
          return (
            <button
              key={item.name}
              type="button"
              onClick={() => setAlgorithm(item.name)}
              style={{
                textAlign: 'left',
                cursor: 'pointer',
                padding: '16px 18px',
                borderRadius: 'var(--radius-signature)',
                border: `1px solid ${active ? 'var(--c-blue)' : 'var(--c-lightest-gray)'}`,
                background: active ? 'rgba(24, 99, 220, 0.05)' : 'var(--c-white)',
              }}
            >
              <div className="row-between" style={{ marginBottom: 8, gap: 8 }}>
                <span style={{ fontSize: 15.5, color: active ? 'var(--c-blue)' : 'var(--c-black)' }}>
                  {item.label}
                </span>
                <span className="tag">{item.task_type}</span>
              </div>
              <div className="caption" style={{ lineHeight: 1.6 }}>
                {item.description}
              </div>
            </button>
          )
        })}
      </div>

      <ParameterCard title={`PARAMETERS · ${current.label}`}>
        {current.requires_target && (
          <TargetSelect value={target} onChange={setTarget} available={available} />
        )}

        {current.params.includes('maxDepth') && (
          <SliderField
            label={`树的深度上限：${maxDepth}（越深拟合越强，也越容易过拟合）`}
            min={1}
            max={20}
            value={maxDepth}
            onChange={setMaxDepth}
          />
        )}

        {current.params.includes('nEstimators') && (
          <SliderField
            label={`树的数量：${nEstimators}（越多越稳，训练越慢）`}
            min={10}
            max={300}
            step={10}
            value={nEstimators}
            onChange={setNEstimators}
          />
        )}

        {current.params.includes('threshold') && (
          <div style={{ marginBottom: 20, maxWidth: 320 }}>
            <label className="field-label" htmlFor="threshold-input">
              二分类阈值（留空则用目标列中位数）
            </label>
            <input
              id="threshold-input"
              className="input"
              placeholder={`例如 90，留空自动取中位数`}
              value={threshold}
              onChange={(event) => setThreshold(event.target.value)}
            />
            <div className="caption" style={{ marginTop: 6 }}>
              大于阈值为类别 1，否则为 0。阈值取得过极端会让少数类样本过少而无法训练。
            </div>
          </div>
        )}

        {current.params.includes('nClusters') && (
          <div style={{ marginBottom: 20, maxWidth: 320 }}>
            <label className="field-label" htmlFor="clusters-input">
              聚类数 k（留空则按轮廓系数自动选择）
            </label>
            <input
              id="clusters-input"
              className="input"
              placeholder="例如 3，留空自动在 2~6 中择优"
              value={nClusters}
              onChange={(event) => setNClusters(event.target.value)}
            />
          </div>
        )}

        <FeaturePicker
          available={current.requires_target ? available.filter((name) => name !== target) : available}
          selected={selected}
          onChange={(next) => {
            setFeaturesTouched(true)
            setSelected(next)
          }}
          label="特征字段（预选了后端默认特征集；清空则由后端使用默认值）"
        />

        <div className="row-between" style={{ flexWrap: 'wrap', gap: 12 }}>
          <RunButton
            loading={loading}
            idleText={`训练 ${current.label}`}
            runningText={`训练中… 已用 ${elapsed}s`}
            onClick={run}
          />
          {current.supports_task_auto && (
            <span className="caption">
              任务类型按目标列取值个数自动判定：离散少量取值走分类，连续量走回归
            </span>
          )}
        </div>
      </ParameterCard>

      {error && <ErrorAlert title="建模失败" message={error} />}
      {loading && <Loading text={`正在训练 ${current.label}…`} />}

      {result && !loading && <TrainResultView result={result} />}
    </>
  )
}

// ---------------------------------------------------------------------------
// 建模结果渲染
// ---------------------------------------------------------------------------
function TrainResultView({ result }: { result: TrainResult }) {
  const isClustering = result.model_task === 'clustering'
  const isClassification = result.model_task === 'classification'

  return (
    <div className="stack" style={{ gap: 24 }}>
      <div className="grid grid-4">
        <StatCard label="算法" value={<span style={{ fontSize: 20 }}>{result.algorithm}</span>} />
        <StatCard label="任务类型" value={<span style={{ fontSize: 20 }}>{result.task_type}</span>} />
        {isClustering ? (
          <>
            <StatCard
              label="聚类数 k"
              value={result.n_clusters ?? '—'}
              hint={result.auto_selected ? '按轮廓系数自动选择' : '用户指定'}
            />
            <StatCard
              label="轮廓系数"
              value={(result.silhouette ?? 0).toFixed(4)}
              hint={
                (result.silhouette ?? 0) >= 0.5
                  ? '簇分离良好'
                  : (result.silhouette ?? 0) >= 0.25
                    ? '簇结构一般'
                    : '簇重叠较多'
              }
            />
          </>
        ) : isClassification ? (
          <>
            <StatCard
              label="准确率"
              value={(result.accuracy ?? 0).toFixed(4)}
              hint="测试集整体判对比例"
            />
            <StatCard label="F1 (macro)" value={(result.f1_macro ?? 0).toFixed(4)} hint="各类别平均，越小说明某类差" />
          </>
        ) : (
          <>
            <StatCard
              label="R² 拟合优度"
              value={(result.r2_score ?? 0).toFixed(4)}
              hint={
                (result.r2_score ?? 0) >= 0.7 ? '拟合较好' : (result.r2_score ?? 0) >= 0.4 ? '拟合一般' : '拟合较弱'
              }
            />
            <StatCard label="RMSE" value={(result.rmse ?? 0).toFixed(4)} hint="均方根误差，越小越好" />
          </>
        )}
      </div>

      <InterpretationCard>
        <ModelInterpretation result={result} />
      </InterpretationCard>

      {/* 特征重要性（树模型） */}
      {result.feature_importance && <ImportanceChart importance={result.feature_importance} />}

      {/* 分类分布 */}
      {result.class_distribution && (
        <div className="card">
          <div className="eyebrow" style={{ marginBottom: 12 }}>
            CLASS DISTRIBUTION · 训练集类别分布
          </div>
          <div className="row" style={{ gap: 10, flexWrap: 'wrap' }}>
            {Object.entries(result.class_distribution).map(([label, count]) => (
              <span className="tag" key={label}>
                {label}：{count.toLocaleString('zh-CN')} 条
              </span>
            ))}
          </div>
          {result.label_definition && (
            <div className="caption" style={{ marginTop: 10 }}>
              标签定义：{result.label_definition.join('  /  ')}
              {result.label_derived && '（目标列非天然二分类，已按阈值二分）'}
            </div>
          )}
        </div>
      )}

      {/* 逻辑回归系数 */}
      {!isClustering && result.coefficients && Object.keys(result.coefficients).length > 0 && (
        <CoefficientChart coefficients={result.coefficients} scaled={result.scaled} />
      )}

      {/* 聚类：候选 k 评分 */}
      {result.candidates && result.candidates.length > 0 && (
        <CandidateChart candidates={result.candidates} />
      )}

      {/* 聚类：簇概览 */}
      {isClustering && result.clusters && result.clusters.length > 0 && (
        <div className="card">
          <div className="eyebrow" style={{ marginBottom: 14 }}>
            CLUSTER OVERVIEW · 各簇规模与主导属性
          </div>
          <div className="grid grid-2" style={{ gap: 12 }}>
            {result.clusters.map((cluster) => (
              <div
                key={cluster.cluster}
                style={{
                  border: '1px solid var(--c-lightest-gray)',
                  borderRadius: 'var(--radius-generous)',
                  padding: 14,
                }}
              >
                <div className="row-between" style={{ marginBottom: 8 }}>
                  <span className="mono">簇 {cluster.cluster}</span>
                  <span className="tag">
                    {cluster.size.toLocaleString('zh-CN')} 条 · {(cluster.ratio * 100).toFixed(1)}%
                  </span>
                </div>
                <div className="row" style={{ gap: 6, flexWrap: 'wrap' }}>
                  {Object.entries(cluster)
                    .filter(([key]) => key.startsWith('dominant_'))
                    .slice(0, 4)
                    .map(([key, value]) => (
                      <span className="tag" key={key}>
                        {key.replace('dominant_', '')}: {String(value)}
                      </span>
                    ))}
                </div>
              </div>
            ))}
          </div>
        </div>
      )}

      {/* 聚类：簇间特征均值对比 */}
      {isClustering && result.cluster_profile && result.cluster_profile.length > 0 && (
        <ClusterProfileTable
          profile={result.cluster_profile}
          features={result.feature_columns}
          overall={result.overall_mean ?? {}}
        />
      )}

      {/* 预测样例 */}
      <SampleTable
        samples={result.sample_predictions ?? result.sample_records ?? []}
        title={isClustering ? '聚类结果样例（前 20 条）' : '预测对比样例（测试集前 10 条）'}
      />
    </div>
  )
}

/** 把建模结果翻译成业务可读的解释文字 */
function ModelInterpretation({ result }: { result: TrainResult }) {
  const algorithm = <span className="mono">{result.algorithm}</span>

  if (result.model_task === 'clustering') {
    return (
      <>
        使用 {algorithm} 对 {result.total_records?.toLocaleString('zh-CN')} 条记录、
        {result.feature_columns.length} 个特征做聚类，得到 {result.n_clusters} 个簇，
        轮廓系数 {result.silhouette}。轮廓系数越接近 1 表示簇内越紧、簇间越远；
        {result.silhouette !== undefined && result.silhouette < 0.25
          ? '当前值偏低，说明这些特征下样本区分度有限，簇之间重叠较多，建议结合特征均值对比表判断是否有业务意义的差异。'
          : '当前值表明簇结构较为清晰。'}
        下方「各簇规模」体现分布是否均衡，「簇间特征均值对比」是解释每个簇业务含义的关键。
      </>
    )
  }

  if (result.model_task === 'classification') {
    const classes = result.classes?.length ?? Object.keys(result.class_distribution ?? {}).length
    return (
      <>
        以 <span className="mono">{result.target}</span> 为目标、
        {result.feature_columns.length} 个特征训练 {algorithm}，共 {classes} 个类别。
        测试集准确率 <strong>{result.accuracy?.toFixed(4)}</strong>，
        macro F1 <strong>{result.f1_macro?.toFixed(4)}</strong>
        （precision {(result.precision_macro ?? 0).toFixed(4)} / recall{' '}
        {(result.recall_macro ?? 0).toFixed(4)}）。
        {result.importance_summary && (
          <>
            <br />
            影响最大的特征为：{result.importance_summary}。
          </>
        )}
        {result.f1_macro !== undefined && result.accuracy !== undefined &&
          result.accuracy - result.f1_macro > 0.15 && (
            <>
              <br />
              注意：accuracy 明显高于 macro F1，说明各类别表现不均衡，少数类识别较差，
              这类模型在业务上可能漏判关键异常，建议补充样本或调整类别权重。
            </>
          )}
      </>
    )
  }

  return (
    <>
      以 <span className="mono">{result.target}</span> 为目标、
      {result.feature_columns.length} 个特征训练 {algorithm}。
      R² = <strong>{result.r2_score?.toFixed(4)}</strong>（解释约{' '}
      {((result.r2_score ?? 0) * 100).toFixed(1)}% 的目标变异），
      RMSE = {result.rmse?.toFixed(4)}。
      {result.importance_summary && (
        <>
          <br />
          重要性排序：{result.importance_summary}。
        </>
      )}
      {(result.r2_score ?? 0) < 0.4 &&
        ' 当前拟合较弱，建议更换特征组合或改用非线性模型。'}
    </>
  )
}

// ---------------------------------------------------------------------------
// 小区块
// ---------------------------------------------------------------------------
function ParameterCard({
  title,
  hint,
  children,
}: {
  title: string
  hint?: string
  children: React.ReactNode
}) {
  return (
    <div className="card" style={{ marginBottom: 24 }}>
      <div className="eyebrow" style={{ marginBottom: 16 }}>
        {title}
      </div>
      {hint && (
        <div className="caption" style={{ marginBottom: 16 }}>
          {hint}
        </div>
      )}
      {children}
    </div>
  )
}

function InterpretationCard({ children }: { children: React.ReactNode }) {
  return (
    <div className="card" style={{ marginBottom: 24 }}>
      <div className="eyebrow" style={{ marginBottom: 8 }}>
        RESULT INTERPRETATION
      </div>
      <h3 className="feature-title" style={{ marginBottom: 10 }}>
        结果解释
      </h3>
      <p style={{ fontSize: 15, lineHeight: 1.75, color: 'var(--c-near-black)', margin: 0 }}>
        {children}
      </p>
    </div>
  )
}

function ErrorAlert({ title, message }: { title: string; message: string }) {
  return (
    <div className="alert alert-danger" style={{ marginBottom: 24 }}>
      <strong>{title}。</strong> {message}
    </div>
  )
}

function RunButton({
  loading,
  idleText,
  runningText,
  onClick,
}: {
  loading: boolean
  idleText: string
  runningText: string
  onClick: () => void
}) {
  return (
    <button type="button" className="btn btn-dark" onClick={onClick} disabled={loading}>
      {loading ? runningText : idleText}
    </button>
  )
}

function SliderField({
  label,
  min,
  max,
  step = 1,
  value,
  onChange,
}: {
  label: string
  min: number
  max: number
  step?: number
  value: number
  onChange: (value: number) => void
}) {
  return (
    <div style={{ marginBottom: 20 }}>
      <label className="field-label">{label}</label>
      <input
        type="range"
        min={min}
        max={max}
        step={step}
        value={value}
        onChange={(event) => onChange(Number(event.target.value))}
        style={{ width: '100%', maxWidth: 420 }}
      />
    </div>
  )
}

function TargetSelect({
  value,
  onChange,
  available,
}: {
  value: string
  onChange: (value: string) => void
  available: string[]
}) {
  const options = Array.from(
    new Set(['defect_rate', 'quality_score', 'downtime_minutes', 'first_pass_yield', 'fault_event_count', ...available]),
  )
  return (
    <div style={{ marginBottom: 20, maxWidth: 360 }}>
      <label className="field-label" htmlFor="target-select">
        预测目标字段
      </label>
      <select
        id="target-select"
        className="select"
        value={value}
        onChange={(event) => onChange(event.target.value)}
      >
        {options.map((name) => (
          <option key={name} value={name}>
            {name}
          </option>
        ))}
      </select>
    </div>
  )
}

function FeaturePicker({
  available,
  selected,
  onChange,
  label,
}: {
  available: string[]
  selected: string[]
  onChange: (next: string[]) => void
  label: string
}) {
  return (
    <div style={{ marginBottom: 20 }}>
      <div className="field-label">{label}</div>
      <div className="row" style={{ flexWrap: 'wrap', gap: 6, maxHeight: 180, overflowY: 'auto' }}>
        {available.map((name) => {
          const active = selected.includes(name)
          return (
            <button
              key={name}
              type="button"
              className={`tag ${active ? 'tag-accent' : ''}`}
              style={{ cursor: 'pointer' }}
              onClick={() =>
                onChange(
                  active ? selected.filter((item) => item !== name) : [...selected, name],
                )
              }
            >
              {name}
            </button>
          )
        })}
      </div>
      {selected.length > 0 && (
        <button
          type="button"
          className="btn btn-ghost btn-sm"
          style={{ paddingLeft: 0, marginTop: 10 }}
          onClick={() => onChange([])}
        >
          清空选择（使用后端默认特征集）· 已选 {selected.length} 个
        </button>
      )}
    </div>
  )
}

function ImportanceChart({ importance }: { importance: Record<string, number> }) {
  const option = useMemo(() => {
    const entries = Object.entries(importance).filter(([, value]) => value > 0).slice(0, 15)
    if (!entries.length) return null
    return {
      tooltip: { trigger: 'axis', axisPointer: { type: 'shadow' } },
      grid: { left: 150, right: 40, top: 16, bottom: 32, containLabel: false },
      xAxis: { type: 'value', name: '重要性' },
      yAxis: { type: 'category', data: entries.map(([name]) => name).reverse(), axisLabel: { fontSize: 11 } },
      series: [
        {
          type: 'bar',
          data: entries.map(([, value]) => value).reverse(),
          itemStyle: { color: colors.interactionBlue },
        },
      ],
    }
  }, [importance])

  if (!option) return null

  return (
    <div className="card">
      <div className="eyebrow" style={{ marginBottom: 12 }}>
        FEATURE IMPORTANCE · 特征重要性（前 15）
      </div>
      <EChart option={option} height={Math.max(240, Object.keys(importance).length * 26)} />
    </div>
  )
}

function CoefficientChart({
  coefficients,
  scaled,
}: {
  coefficients: Record<string, number>
  scaled?: boolean
}) {
  const option = useMemo(() => {
    const entries = Object.entries(coefficients)
    if (!entries.length) return null
    entries.sort((a, b) => Math.abs(b[1]) - Math.abs(a[1]))
    return {
      tooltip: { trigger: 'axis', axisPointer: { type: 'shadow' } },
      grid: { left: 150, right: 48, top: 16, bottom: 32, containLabel: false },
      xAxis: { type: 'value', name: '系数' },
      yAxis: { type: 'category', data: entries.map(([name]) => name), axisLabel: { fontSize: 11 } },
      series: [
        {
          type: 'bar',
          data: entries.map(([, value]) => ({
            value,
            itemStyle: { color: value >= 0 ? colors.interactionBlue : colors.focusPurple },
          })),
          label: { show: true, position: 'right', fontSize: 11, formatter: '{c}' },
        },
      ],
    }
  }, [coefficients])

  if (!option) return null

  return (
    <div className="card">
      <div className="eyebrow" style={{ marginBottom: 12 }}>
        COEFFICIENTS · 特征系数{scaled ? '（已标准化，可按绝对值比较重要性）' : ''}
      </div>
      <div style={{ fontSize: 14, color: 'var(--c-muted-slate)', marginBottom: 12 }}>
        蓝色表示该特征增大时目标/类别概率上升，紫色表示下降。
      </div>
      <EChart option={option} height={Math.max(260, Object.keys(coefficients).length * 30)} />
    </div>
  )
}

function CandidateChart({
  candidates,
}: {
  candidates: { k: number; silhouette: number; inertia: number }[]
}) {
  const option = useMemo(
    () => ({
      tooltip: { trigger: 'axis' },
      legend: { data: ['轮廓系数', '簇内平方和'], top: 0 },
      grid: { left: 56, right: 56, top: 44, bottom: 40, containLabel: true },
      xAxis: { type: 'category', data: candidates.map((item) => `k=${item.k}`) },
      yAxis: [
        { type: 'value', name: '轮廓系数', min: 0 },
        { type: 'value', name: '簇内平方和' },
      ],
      series: [
        {
          name: '轮廓系数',
          type: 'bar',
          data: candidates.map((item) => item.silhouette),
          itemStyle: { color: colors.interactionBlue },
        },
        {
          name: '簇内平方和',
          type: 'line',
          yAxisIndex: 1,
          data: candidates.map((item) => item.inertia),
          smooth: true,
          itemStyle: { color: colors.focusPurple },
        },
      ],
    }),
    [candidates],
  )

  return (
    <div className="card">
      <div className="eyebrow" style={{ marginBottom: 12 }}>
        CHOOSING K · 不同聚类数的评分（轮廓系数越大越好）
      </div>
      <EChart option={option} height={300} />
    </div>
  )
}

function ClusterProfileTable({
  profile,
  features,
  overall,
}: {
  profile: Record<string, unknown>[]
  features: string[]
  overall: Record<string, number>
}) {
  const columns = ['特征', ...profile.map((item) => `簇 ${item.cluster}`), '整体均值']
  const rows = features.map((name) => [
    name,
    ...profile.map((item) => Number(item[name] ?? 0).toFixed(3)),
    overall[name] !== undefined ? overall[name].toFixed(3) : '—',
  ])

  return (
    <>
      <div className="eyebrow" style={{ marginBottom: 12 }}>
        CLUSTER PROFILE · 簇间特征均值对比（解释每个簇的业务含义）
      </div>
      <DataTable columns={columns} rows={rows} />
    </>
  )
}

function SampleTable({
  samples,
  title,
}: {
  samples: Record<string, unknown>[]
  title: string
}) {
  if (!samples.length) return null
  const columns = Object.keys(samples[0])
  return (
    <>
      <div className="eyebrow">{title}</div>
      <DataTable
        columns={columns}
        rows={samples.map((record) => columns.map((column) => record[column]))}
      />
    </>
  )
}
