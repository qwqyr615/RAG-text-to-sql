# 企业数据底座智能问析 Agent 系统 · 前端

基于 **React 18 + TypeScript + Vite** 的前端工程，视觉严格遵循 `FRONTEND/DESIGN.md`
定义的 **Cohere 设计系统**。

## 在整体架构中的位置

```text
React 前端（本工程，:5173）
    ↓ HTTP / SSE  /api/v1/**
Spring Boot 网关（backend/，:8080）
    ↓ HTTP / SSE
FastAPI 适配层（agent/server/，:8000）
    ↓ 函数直接调用
Agent 内核（agent/agents、metadata、knowledge、tools）
    ↓
MySQL + Milvus + DeepSeek
```

前端只请求 **Java 网关**，不直连 FastAPI。开发期由 Vite devServer 把 `/api` 代理到
`http://127.0.0.1:8080`（见 `vite.config.ts`），生产部署时由 Nginx 承担同样的转发职责。

## 快速开始

```powershell
cd FRONTEND
pnpm install
pnpm dev          # http://127.0.0.1:5173
```

> **启动顺序很重要**：先启动 FastAPI（`cd agent && python -m uvicorn server.main:app --port 8000`），
> 再启动 Java 网关（`cd backend && mvn spring-boot:run`），最后才是前端。
> 若后端未就绪，页面顶部会直接给出可执行的排查提示，而不是白屏。

其他命令：

```powershell
pnpm typecheck    # tsc --noEmit 类型检查
pnpm build        # 生产构建（输出 dist/）
pnpm preview      # 预览生产构建
```

## 页面与接口对应

| 页面 | 路由 | 依赖接口 |
|---|---|---|
| 总览 | `/` | `/system/health`、`/metadata/tables`、`/knowledge/overview` |
| 智能问答 | `/chat` | `POST /agent/ask/async`、`GET /agent/jobs/{id}`、`GET /agent/jobs/{id}/stream` |
| 数据资源 | `/metadata` | `/metadata/tables`、`/metadata/relationships` |
| 业务知识 | `/knowledge` | `/knowledge/overview`、`/knowledge/graph` |
| 机器建模 | `/modeling` | `/modeling/features`、`POST /modeling/anomaly`、`POST /modeling/regression` |
| 分析报告 | `/report` | `POST /agent/report`（内部走异步任务链路） |

## 目录结构

```text
FRONTEND/
├── DESIGN.md                 # 设计系统规范（Cohere），所有样式的依据
├── index.html                # 入口 HTML
├── vite.config.ts            # 构建与 /api 代理配置
├── tsconfig.json
└── src/
    ├── main.tsx              # React 挂载入口
    ├── App.tsx               # 路由表
    ├── api/
    │   ├── types.ts          # 后端契约类型（刻意保持 snake_case）
    │   └── client.ts         # 接口封装 + 信封解包 + SSE/轮询订阅
    ├── hooks/
    │   ├── useAsk.ts         # 问析共享逻辑（异步提交 → 订阅 → 结果）
    │   └── useAsync.ts       # 通用三态数据加载
    ├── components/
    │   ├── AppShell.tsx      # 顶部导航 + 服务健康状态 + 页脚
    │   ├── EChart.tsx        # ECharts 容器（合并 Cohere 主题）
    │   ├── DataTable.tsx     # 数据表格
    │   ├── AnalysisTimeline.tsx  # 分析过程时间线
    │   ├── Markdown.tsx      # 报告 Markdown 渲染
    │   └── StateBlocks.tsx   # 加载/错误/空态/标题/指标卡
    ├── pages/                # 六个页面
    └── styles/
        ├── tokens.ts         # 设计令牌（色彩/字阶/圆角/间距/图表主题）
        └── global.css        # 全局样式与组件类
```

## 设计系统落地说明

实现依据是 `DESIGN.md`，关键约束都已落到代码里：

- **签名圆角 22px**：所有主卡片与主容器使用 `--radius-signature`。
- **近乎无阴影**：层次靠 `1px` 冷灰描边（`#f2f2f2` / `#d9d9dd`）与背景色对比表达，
  不靠 `box-shadow`。
- **色彩极度克制**：黑白 + 冷灰为主；`#1863dc` **只**出现在 hover / focus / active；
  紫色仅用于整幅区块（`AppShell` 之外的 `.purple-band`，目前用在总览页 hero），
  **绝不作为卡片背景**。
- **双字阶**：标题用衬线（`CohereText` 风格），正文用无衬线（`Unica77` 风格），
  技术标签用等宽大写（`CohereMono` 风格，`.eyebrow`）。
- **正文不做 700+ 字重**：只用 400 与 500（后者仅小号按钮）。

### 字体降级（重要）

`CohereText` / `Unica77 Cohere Web` / `CohereMono` 是 **Cohere 的私有字体，无法获取**。
因此按 DESIGN.md 给出的 fallback 链降级为开源等价字体：

| 角色 | 设计规范 | 实际使用 |
|---|---|---|
| Display | CohereText | `Space Grotesk` → Inter → system-ui |
| Body / UI | Unica77 Cohere Web | `Inter` → Arial → system-ui |
| Code / Label | CohereMono | `JetBrains Mono` → Arial → system-ui |

「衬线标题 + 无衬线正文」的视觉性格得以保留。若后续获得授权字体，
只需替换 `src/styles/tokens.ts` 的 `fonts` 与 `global.css` 的 `--font-*` 变量。

### 图表配色

ECharts 主题定义在 `tokens.ts` 的 `chartTheme`，同样严格冷色：
主色交互蓝 `#1863dc`，辅以冷灰与紫色（`#9b60aa`）。
图表的 `option` 由**后端大模型生成**（`agent/agents/chart_agent.py`），
前端 `EChart` 组件只负责合并主题与渲染，不参与业务语义判断。

## 关键实现说明

### 为什么问析必须走异步 + SSE

实测一次提问（"找出停机时间最长的 10 台设备"）后端耗时 **94 秒**，
超过 `SQL_AGENT_MAX_EXECUTION_TIME`（默认 90 秒）。
若用同步 `fetch`，前端与 Java 网关都会超时。因此：

1. `POST /api/v1/agent/ask/async` 立即返回 `job_id`；
2. 前端用 `EventSource` 订阅 `/api/v1/agent/jobs/{id}/stream` 接收进度；
3. 若 SSE 不可用（浏览器限制或连接中断），自动降级为轮询 `/agent/jobs/{id}`。

同时前端会显示**实测计时秒数**（后端进度是阶段式的，`20% → 85% → 100%`），
避免用户误以为页面卡死。该逻辑集中在 `hooks/useAsk.ts` 与 `api/client.ts` 的
`subscribeJob`。

### 契约字段保持 snake_case

`src/api/types.ts` 中的字段名**刻意与后端返回一致**（`table_name`、`row_count`、
`chart_config`、`analysis_steps`…），不做驼峰转换。这样能直接对照
FastAPI 的 `/docs` 与 Java 网关的接口排查问题，也避免转换层引入隐性 bug。

### 知识图谱是「逻辑图谱」

`dim_*` 维表已删除，当前是单表宽表模型，**实体关系图没有数据支撑**。
因此 `/knowledge/graph` 返回的是「主题 → 业务对象 → 指标口径 → 字段 → 数据表」
的逻辑图谱，每个节点都来自真实的 metadata / knowledge 解析结果。
未解析到字段的指标以**空心虚线节点**呈现，直观暴露数据缺口。
