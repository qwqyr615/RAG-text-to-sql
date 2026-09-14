/**
 * 路由表。
 *
 * 页面与接口的对应关系（与后端 `docs/API_DESIGN.md` 的「前端页面预留」一致）：
 *  - 总览     能力入口 + 服务状态
 *  - 智能问答 /agent/*            核心闭环：提问 → 分析 → 图表/结论
 *  - 数据资源 /metadata/*         表、字段、类型、说明、样例值
 *  - 业务知识 /knowledge/*        主题、对象、指标规则 + 知识图谱
 *  - 机器建模 /modeling/*         异常检测、回归建模
 *  - 分析报告 /agent/report       Markdown 报告生成与导出
 */

import { Navigate, Route, Routes } from 'react-router-dom'
import { BrowserRouter } from 'react-router-dom'

import { AppShell } from './components/AppShell'
import { ChatPage } from './pages/ChatPage'
import { DashboardPage } from './pages/DashboardPage'
import { KnowledgePage } from './pages/KnowledgePage'
import { MetadataPage } from './pages/MetadataPage'
import { ModelingPage } from './pages/ModelingPage'
import { ReportPage } from './pages/ReportPage'

export function App() {
  return (
    <BrowserRouter>
      <Routes>
        <Route element={<AppShell />}>
          <Route index element={<DashboardPage />} />
          <Route path="chat" element={<ChatPage />} />
          <Route path="metadata" element={<MetadataPage />} />
          <Route path="knowledge" element={<KnowledgePage />} />
          <Route path="modeling" element={<ModelingPage />} />
          <Route path="report" element={<ReportPage />} />
          <Route path="*" element={<Navigate to="/" replace />} />
        </Route>
      </Routes>
    </BrowserRouter>
  )
}
