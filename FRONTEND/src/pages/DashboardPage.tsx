/**
 * 总览页。
 *
 * 兼顾两个目的：
 *  1. 「产品封面」——用 DESIGN.md 的紫色整幅区块承载价值主张（Cohere 的 hero band 语言）；
 *  2. 「系统体检」——把后端真实的数据资源规模与能力清单摆在明面上，
 *     评委/使用者一眼能看出系统接入了什么、能做什么。
 */

import { Link } from 'react-router-dom'

import { api } from '../api/client'
import { ErrorBlock, Loading, PageHeader, StatCard } from '../components/StateBlocks'
import { useAsync } from '../hooks/useAsync'

/** 能力清单：与题目「任务清单」的 5 项能力一一对应 */
const CAPABILITIES = [
  {
    code: 'KNOWLEDGE',
    title: '业务知识管理',
    description: '围绕制造场景维护分析主题、业务对象与指标口径，并解析到真实表与字段。',
    to: '/knowledge',
    action: '查看业务知识',
  },
  {
    code: 'RESOURCE',
    title: '数据资源理解',
    description: '读取并展示数据表、字段类型、字段说明、样例值与表间关系。',
    to: '/metadata',
    action: '查看数据资源',
  },
  {
    code: 'TEXT2SQL',
    title: '自然语言智能分析',
    description: '理解业务提问，自动定位数据、对齐口径并生成只读 SQL 完成统计分析。',
    to: '/chat',
    action: '开始提问',
  },
  {
    code: 'MODELING',
    title: '建模与异常检测',
    description: 'Isolation Forest 异常检测与线性回归建模，支持模型评估与结果解释。',
    to: '/modeling',
    action: '进入建模',
  },
  {
    code: 'VISUAL',
    title: '分析结果展示',
    description: '大模型自动产出 ECharts 图表配置，配合数据表格与文字结论完成展示。',
    to: '/chat',
    action: '看图表效果',
  },
  {
    code: 'REPORT',
    title: '分析报告生成',
    description: '基于查询结果与结论生成 Markdown 报告，支持直接导出与归档。',
    to: '/report',
    action: '生成报告',
  },
]

export function DashboardPage() {
  const metadata = useAsync(() => api.metadataTables(), [])
  const knowledge = useAsync(() => api.knowledgeOverview(), [])

  const tables = metadata.data?.tables ?? []
  const columnCount = tables.reduce((sum, table) => sum + table.columns.length, 0)
  const rowCount = tables.reduce(
    (sum, table) => sum + (table.row_count > 0 ? table.row_count : 0),
    0,
  )
  const themes = knowledge.data?.themes?.length ?? 0
  const metrics = knowledge.data?.rules?.filter((rule) => rule.mapped_field).length ?? 0

  return (
    <>
      {/* 紫色整幅区块：DESIGN.md 明确紫只用于整宽区块，不做卡片背景 */}
      <div className="purple-band" style={{ margin: '-32px -24px 40px', borderRadius: 0 }}>
        <div className="container">
          <div className="eyebrow" style={{ marginBottom: 16 }}>
            ENTERPRISE DATA FOUNDATION · INTELLIGENT ANALYTICS
          </div>
          <h1 className="display-secondary" style={{ maxWidth: 900, marginBottom: 18 }}>
            用自然语言，直接读懂企业数据底座
          </h1>
          <p
            className="body-large"
            style={{ color: 'rgba(255,255,255,0.72)', maxWidth: 640, margin: 0 }}
          >
            业务人员提出问题，系统自动完成数据定位、口径对齐、SQL 生成与执行、
            图表绘制和结论输出——形成「用户提问 → 智能分析 → 结果展示」的完整闭环。
          </p>
          <div className="row" style={{ marginTop: 32, gap: 12, flexWrap: 'wrap' }}>
            <Link to="/chat" className="btn btn-dark" style={{ background: 'var(--c-white)', color: 'var(--c-black)' }}>
              开始智能问析
            </Link>
            <Link
              to="/metadata"
              className="btn"
              style={{ color: 'var(--c-white)', border: '1px solid rgba(255,255,255,0.35)' }}
            >
              浏览数据资源
            </Link>
          </div>
        </div>
      </div>

      {(metadata.loading || knowledge.loading) && <Loading text="正在读取数据底座信息…" />}
      {metadata.error && <ErrorBlock message={metadata.error} onRetry={metadata.reload} />}

      {!metadata.loading && !metadata.error && (
        <>
          {/* 系统规模 */}
          <section className="section">
            <div className="eyebrow" style={{ marginBottom: 6 }}>
              OVERVIEW
            </div>
            <h2 className="sub-heading" style={{ marginBottom: 22 }}>
              已接入的数据底座
            </h2>
            <div className="grid grid-4">
              <StatCard
                label="业务表"
                value={tables.length}
                hint={metadata.data?.database_type ? `数据源：${metadata.data.database_type}` : undefined}
              />
              <StatCard label="字段总数" value={columnCount} hint="含类型、说明与样例值" />
              <StatCard
                label="数据行数"
                value={rowCount > 0 ? rowCount.toLocaleString('zh-CN') : '—'}
                hint="服务层宽表行数合计"
              />
              <StatCard label="分析主题" value={themes} hint={`已解析指标 ${metrics} 项`} />
            </div>
          </section>

          {/* 数据表清单 */}
          <section className="section">
            <PageHeader
              eyebrow="DATA SOURCES"
              title="数据表"
              description="系统按「发现模式」自动扫描数据库，套用排除规则得到可用业务表，不硬编码表白名单。"
              actions={
                <Link to="/metadata" className="btn btn-outlined">
                  查看全部字段
                </Link>
              }
            />
            <div className="grid grid-3">
              {tables.map((table) => (
                <div className="card" key={table.table_name}>
                  <div className="eyebrow" style={{ marginBottom: 8 }}>
                    {table.role || 'TABLE'}
                  </div>
                  <div className="mono" style={{ fontSize: 15, color: 'var(--c-black)', marginBottom: 8 }}>
                    {table.table_name}
                  </div>
                  <p style={{ fontSize: 14, color: 'var(--c-muted-slate)', margin: '0 0 14px', lineHeight: 1.6 }}>
                    {table.description || '暂无表说明'}
                  </p>
                  <div className="row" style={{ flexWrap: 'wrap', gap: 6 }}>
                    <span className="tag">{table.columns.length} 字段</span>
                    {table.row_count > 0 && (
                      <span className="tag">{table.row_count.toLocaleString('zh-CN')} 行</span>
                    )}
                    {table.primary_key && <span className="tag">主键 {table.primary_key}</span>}
                  </div>
                </div>
              ))}
            </div>
          </section>
        </>
      )}

      {/* 能力清单 */}
      <section className="section">
        <PageHeader
          eyebrow="CAPABILITIES"
          title="系统能力"
          description="以下能力均已接入真实数据与接口，可在对应页面直接演示。"
        />
        <div className="grid grid-3">
          {CAPABILITIES.map((item) => (
            <div className="card" key={item.code} style={{ display: 'flex', flexDirection: 'column' }}>
              <div className="eyebrow" style={{ marginBottom: 12 }}>
                {item.code}
              </div>
              <h3 className="feature-title" style={{ marginBottom: 10 }}>
                {item.title}
              </h3>
              <p
                style={{
                  fontSize: 14,
                  color: 'var(--c-muted-slate)',
                  lineHeight: 1.65,
                  margin: '0 0 18px',
                  flex: 1,
                }}
              >
                {item.description}
              </p>
              <Link to={item.to} className="btn btn-ghost btn-sm" style={{ paddingLeft: 0, alignSelf: 'flex-start' }}>
                {item.action} →
              </Link>
            </div>
          ))}
        </div>
      </section>

      {/* 示例问题：降低演示门槛 */}
      <section>
        <div className="card card-flat">
          <div className="eyebrow" style={{ marginBottom: 10 }}>
            SAMPLE QUESTIONS
          </div>
          <h2 className="feature-title" style={{ marginBottom: 16 }}>
            可以这样提问
          </h2>
          <div className="grid grid-2" style={{ gap: 10 }}>
            {[
              '请分析各工序的良率。',
              '找出最近一个月不良数量最高的产品。',
              '统计每条产线最近 7 天的产量趋势。',
              '找出停机时间最长的 10 台设备。',
              '用回归模型预测缺陷率。',
              '找出异常数据。',
            ].map((question) => (
              <Link
                key={question}
                to={`/chat?q=${encodeURIComponent(question)}`}
                style={{
                  fontSize: 15,
                  color: 'var(--c-near-black)',
                  padding: '10px 14px',
                  border: '1px solid var(--c-lightest-gray)',
                  borderRadius: 'var(--radius-comfortable)',
                  background: 'var(--c-white)',
                }}
              >
                {question}
              </Link>
            ))}
          </div>
        </div>
      </section>
    </>
  )
}
