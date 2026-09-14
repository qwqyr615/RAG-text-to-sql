# 企业数据底座智能问析 Agent 系统 —— Java 网关（backend）

本模块是「企业数据底座智能问析 Agent 系统」的 **Java 网关服务**：

```
React 前端 (Vite, :5173)
        │  统一 REST 契约 {code, msg, data}
        ▼
Java 网关 (Spring Boot 3.2.3, :8080)      ← 本模块
        │  RestTemplate + 连接池 + 超时
        ▼
Python Agent 内核的 FastAPI 适配层 (:8000)  ← agent/server/（已存在，本模块不修改）
        │
        ▼
Agent 内核 / MySQL / 大模型
```

网关的职责很纯粹：**对外只暴露一种契约，向下调用 FastAPI 并原样透传 `{code, msg, data}` 信封**。
前端不需要知道 Python 的存在，也不需要区分两套返回结构。

---

## 1. 模块结构

Maven 多模块，工程规范参照「苍穹外卖」(sky-take-out)：

```
backend/
├── pom.xml                     父工程：spring-boot-starter-parent 3.2.3，packaging=pom
│                               <dependencyManagement> 统一管理版本
├── agent-common/               通用模块（jar）
│   └── src/main/java/com/agent/
│       ├── result/Result.java         统一返回结果 code(1成功/0失败) / msg / data
│       ├── exception/BaseException.java      业务异常基类
│       ├── exception/AgentApiException.java  上游 Agent 调用异常
│       ├── constant/AgentConstants.java      常量（信封码值、job 状态、SSE 事件名、错误文案）
│       └── util/JsonUtils.java               JSON 工具（显式配置 SNAKE_CASE 的 ObjectMapper）
├── agent-pojo/                 实体模块（jar）
│   └── src/main/java/com/agent/
│       ├── dto/AgentAskDTO.java              问答请求（ask / report / ask/async 共用）
│       ├── dto/AnomalyRequestDTO.java        异常检测请求
│       ├── dto/RegressionRequestDTO.java     回归建模请求
│       └── vo/                               AskResponseVO / ChartConfigVO / AnalysisStepVO
│                                             HealthVO / JobSubmitVO / JobStateVO / SessionResetVO
│                                             TableMetadataVO / ColumnMetadataVO
│                                             ModelingFeatureVO / ModelingFeaturesVO
└── agent-server/               服务模块（可执行 jar，端口 8080）
    └── src/main/
        ├── java/com/agent/
        │   ├── AgentGatewayApplication.java  启动类
        │   ├── config/AgentApiProperties.java    agent.api.* 配置绑定
        │   ├── config/RestTemplateConfig.java    RestTemplate + HttpClient5 连接池与超时
        │   ├── config/WebMvcConfig.java          CORS（放行 Vite 开发服务器）
        │   ├── service/AgentApiClient.java       ★ 所有上游调用的唯一出口
        │   ├── service/AgentSseService.java      ★ SSE 流代理（SseEmitter）
        │   ├── controller/SystemController.java       /system/**
        │   ├── controller/MetadataController.java     /metadata/**
        │   ├── controller/KnowledgeController.java    /knowledge/**
        │   ├── controller/AgentController.java        /agent/**
        │   ├── controller/ModelingController.java     /modeling/**
        │   └── handler/GlobalExceptionHandler.java    @RestControllerAdvice 全局异常
        └── resources/
            ├── application.yml        通用配置（端口、Jackson、日志）
            └── application-dev.yml    开发环境（上游地址、超时、连接池）
```

**分层原则**：Controller 只做参数校验与转发；所有 HTTP 细节（URL 拼装、超时、错误兜底、
信封还原）都收敛在 `AgentApiClient`；SSE 的逐帧转发单独放在 `AgentSseService`。

---

## 2. 环境要求与编译

### 2.1 环境要求

| 项 | 值 |
|---|---|
| JDK | **Java 17 及以上**（本机使用 `E:\develop\jdk`，Oracle JDK 21.0.9 实测通过） |
| 字节码目标 | Java 17（`<java.version>17</java.version>`） |
| Maven | `D:\develop\apache-maven-3.9.11` |
| Spring Boot | 3.2.3（Spring Framework 6.1.4，Tomcat 10.1.19） |
| 服务端口 | **8080**（上游 FastAPI 是 8000） |

> **关于 JDK 版本的重要说明**
> 任务书最初要求「Java 8 编译目标 + Spring Boot 2.7.3 + JAVA_HOME=E:\develop\jdk17」。
> 实际核查发现：**`E:\develop\jdk17` 是一个空目录**（0 个文件，无 `bin/java.exe`），
> 本机唯一可用 JDK 是 `E:\develop\jdk`（Oracle JDK **21**.0.9），且本机外网不通、无法下载 JDK 17。
> Spring Boot 2.7.3 配套的 Spring Framework 5.3 官方仅支持到 Java 19，在 JDK 21 上属于不受支持的组合，
> 因此改用官方支持 JDK 21 的 **Spring Boot 3.2.3 + Java 17 字节码**。
> 架构规范（多模块、包名 `com.agent`、`Result<T>`、全局异常、Lombok、配置分离）全部保持不变；
> 唯一需要适配的是 Spring Boot 3 使用 **`jakarta.*`** 命名空间（不再是 `javax.*`）。

### 2.2 编译打包

```powershell
$env:JAVA_HOME='E:\develop\jdk'
cd 'E:\code\Agent\企业数据底座智能问析 Agent 系统\backend'
mvn -q -DskipTests package          # 有网络时
mvn -o -q package                   # 本机离线仓库已缓存全部依赖时
```

成功后会产出三个 jar：

```
agent-common/target/agent-common-1.0-SNAPSHOT.jar
agent-pojo/target/agent-pojo-1.0-SNAPSHOT.jar
agent-server/target/agent-server.jar        ← 可执行 fat jar（含全部依赖）
```

> **离线构建说明**：本机 Maven 仓库（`E:\develop\mvn_repo`）缺少以下几个构件，
> 项目已在 `pom.xml` 中做了针对性规避，无需联网：
> - `maven-surefire-plugin` 的传递依赖缺失 → 在父 pom 里把 `default-test` execution 绑定到 `phase=none`，
>   彻底摘掉测试插件（本网关以实际接口调用验证，不写单测）。
> - `maven-jar-plugin:3.3.0` 依赖的 `plexus-archiver:4.4.0` 缺失 → 在 `<pluginManagement>` 中
>   将 jar 插件钉到 **3.4.2**（其依赖 `plexus-archiver:4.9.2` 已缓存）。
> - `spring-boot-configuration-processor:3.2.3` 缺失 → 未引入（仅影响 IDE 配置补全，不影响运行）。
> - Spring Boot **3.2.10** 的 `spring-boot-loader-tools` / `spring-boot-buildpack-platform` 在本地
>   只有 pom 没有 jar，会被 `spring-boot:repackage` 需要 → 因此选 **3.2.3**（这两个 jar 已完整缓存）。
> - 未引入 `knife4j` / `springdoc-openapi`（本地缺失），接口清单以本文档第 3 节表格为准。
> - 未引入 `spring-boot-starter-validation`（本地缺失），参数校验在 Controller/Service 层手工完成。

---

## 3. 启动方式

### ⚠️ 必须先启动 FastAPI，再启动 Java 网关

Java 网关本身可以在上游未启动时正常启动（不会崩），
但所有代理接口都会返回：

```json
{"code": 0, "msg": "Agent 服务不可用，请确认 FastAPI 已在 127.0.0.1:8000 启动", "data": null}
```

### 3.1 第一步：启动 Python Agent 内核（FastAPI 适配层）

```powershell
cd 'E:\code\Agent\企业数据底座智能问析 Agent 系统\agent'
$env:PYTHONIOENCODING='utf-8'
D:\Anaconda\envs\sqllangchain\python.exe -m uvicorn server.main:app --host 127.0.0.1 --port 8000
```

启动成功的标志（内核预热约需 15~20 秒）：

```
server.main | FastAPI 适配层启动，正在预热 Agent 内核…
server.deps | Agent 内核就绪：3 张业务表
server.main | 内核就绪：数据库 mysql，业务表 3 张，模型 deepseek-chat
INFO:     Uvicorn running on http://127.0.0.1:8000
```

验证：`curl http://127.0.0.1:8000/api/v1/system/health`

### 3.2 第二步：启动 Java 网关

```powershell
$env:JAVA_HOME='E:\develop\jdk'
cd 'E:\code\Agent\企业数据底座智能问析 Agent 系统\backend\agent-server'
java -jar target\agent-server.jar            # 默认激活 dev profile，端口 8080
```

> 注意：**工作目录含中文**（`企业数据底座智能问析`）。在 PowerShell 中给 `java` 传
> `-D` 参数时务必加引号（如 `"-Dfile.encoding=UTF-8"`），否则 PowerShell 会按 `.` 切分参数
> 导致 `ClassNotFoundException: /encoding=UTF-8`。
>
> 请用 `java -jar` 启动，不要用 `mvn spring-boot:run`：本机离线仓库缺少
> `spring-boot-buildpack-platform` / `spring-boot-loader-tools` / `maven-shade-plugin`，
> `spring-boot:run` 所需的插件依赖无法解析。

启动成功的标志：

```
  .   ____          _            __ _ _
 :: Spring Boot ::                (v3.2.3)
The following 1 profile is active: "dev"
Tomcat initialized with port 8080 (http)
Agent 上游 RestTemplate 已初始化：baseUrl=http://127.0.0.1:8000，连接超时=10000ms，读取超时=180000ms，连接池 maxTotal=200 maxPerRoute=50
Tomcat started on port 8080 (http) with context path ''
  企业数据底座智能问析 Agent 系统 - Java 网关 启动成功
  本地地址   : http://127.0.0.1:8080
  上游 Agent : http://127.0.0.1:8000
```

### 3.3 前端联调

Vite 开发服务器（`http://localhost:5173` / `http://127.0.0.1:5173`）已配置 CORS 放行，
允许全部方法与请求头，并允许携带凭证。

---

## 4. 对外接口清单（给 React 用）

统一前缀 **`/api/v1`**，**响应结构与上游逐字段一致**，不二次包装。
所有响应都是：

```json
{ "code": 1, "msg": "", "data": { ... } }
```

`code=1` 成功；`code=0` 失败且 `msg` 为可读中文错误信息。
**即使上游不可用，网关也返回 HTTP 200 + 上述信封，不会抛 500 堆栈。**

| 方法 | 路径 | 说明 | 上游对应 |
|---|---|---|---|
| GET | `/api/v1/system/health` | 健康检查（内核就绪状态、表数、模型、RAG） | `GET /system/health` |
| GET | `/api/v1/system/ping` | 网关自身存活探针（不依赖上游，用于区分两侧故障） | — 网关本地 |
| GET | `/api/v1/metadata/tables` | 全部表结构 / 字段 / 样例值（**数据资源页主接口**） | `GET /metadata/tables` |
| GET | `/api/v1/metadata/relationships` | 表间关系 | `GET /metadata/relationships` |
| GET | `/api/v1/metadata/field-map` | 标准字段口径映射 | `GET /metadata/field-map` |
| GET | `/api/v1/knowledge/overview` | 主题 / 业务对象 / 指标规则总览 | `GET /knowledge/overview` |
| GET | `/api/v1/knowledge/graph` | 知识图谱 nodes / edges / categories | `GET /knowledge/graph` |
| POST | `/api/v1/agent/ask` | 自然语言问答（**同步，最长 180s**） | `POST /agent/ask` |
| POST | `/api/v1/agent/report` | 生成 Markdown 报告（网关强制 `wantReport=true`） | `POST /agent/report` |
| POST | `/api/v1/agent/ask/async` | 提交异步任务，立即返回 `{job_id, status}` | `POST /agent/ask/async` |
| GET | `/api/v1/agent/jobs/{jobId}` | 查询任务状态与结果 | `GET /agent/jobs/{job_id}` |
| GET | `/api/v1/agent/jobs/{jobId}/stream` | **SSE 进度流**（`progress` / `done` / `error` 事件） | `GET /agent/jobs/{job_id}/stream` |
| DELETE | `/api/v1/agent/session/{sessionId}` | 清空会话多轮历史 | `DELETE /agent/session/{session_id}` |
| POST | `/api/v1/modeling/anomaly` | Isolation Forest 异常检测 | `POST /modeling/anomaly` |
| POST | `/api/v1/modeling/regression` | 线性回归建模与评估 | `POST /modeling/regression` |
| GET | `/api/v1/modeling/features?role=numeric` | 可用建模字段（`role=numeric` 仅数值型） | `GET /modeling/features` |

### 4.1 请求示例

**同步问答**

```bash
curl -X POST http://127.0.0.1:8080/api/v1/agent/ask \
  -H "Content-Type: application/json" \
  -d '{"question":"一共有多少条生产记录？","sessionId":"demo","wantChart":true,"wantReport":false}'
```

请求体字段（camelCase；网关同时兼容 snake_case 入参）：

| 字段 | 类型 | 默认 | 说明 |
|---|---|---|---|
| `question` | string | 必填 | 用户自然语言问题 |
| `sessionId` | string | `default` | 会话 ID，用于多轮上下文 |
| `metadata` | string | `""` | 外部补充的数据资源说明 |
| `businessRules` | string | `""` | 外部补充的业务口径 |
| `wantChart` | boolean | `true` | 是否生成 ECharts 图表配置 |
| `wantReport` | boolean | `false` | 是否生成 Markdown 报告 |

**异步任务 + SSE 订阅（推荐大屏使用）**

```bash
# 1) 提交
curl -X POST http://127.0.0.1:8080/api/v1/agent/ask/async \
  -H "Content-Type: application/json" \
  -d '{"question":"各产线的平均良率是多少？","sessionId":"demo"}'
# → {"code":1,"msg":"","data":{"job_id":"0835...","status":"pending"}}

# 2) 订阅进度（EventSource 直接可用）
curl -N http://127.0.0.1:8080/api/v1/agent/jobs/0835.../stream
```

```javascript
// 前端：契约与直连 FastAPI 完全一致，无需改动
const es = new EventSource(`http://127.0.0.1:8080/api/v1/agent/jobs/${jobId}/stream`);
es.addEventListener('progress', e => setStage(JSON.parse(e.data)));   // 进度
es.addEventListener('done',     e => { render(JSON.parse(e.data)); es.close(); });
es.addEventListener('error',    e => { alert(JSON.parse(e.data).error); es.close(); });
```

**异常检测 / 回归**

```bash
curl -X POST http://127.0.0.1:8080/api/v1/modeling/anomaly \
  -H "Content-Type: application/json" -d '{"contamination":0.05,"limit":2000}'

curl -X POST http://127.0.0.1:8080/api/v1/modeling/regression \
  -H "Content-Type: application/json" -d '{"target":"defect_rate","limit":1000,"testSize":0.2}'
```

### 4.2 响应示例（`GET /api/v1/system/health`）

```json
{
  "code": 1,
  "msg": "",
  "data": {
    "status": "ok",
    "agent_ready": true,
    "database_type": "mysql",
    "table_count": 3,
    "llm_model": "deepseek-chat",
    "rag_enabled": true,
    "error": null
  }
}
```

### 4.3 上游不可用时的响应

所有代理接口都返回（HTTP 200，无堆栈）：

```json
{ "code": 0, "msg": "Agent 服务不可用，请确认 FastAPI 已在 127.0.0.1:8000 启动", "data": null }
```

SSE 接口则先推一个 `error` 事件再关闭连接，避免前端静默卡住：

```
event:error
data:{"error":"Agent 服务不可用，请确认 FastAPI 已在 127.0.0.1:8000 启动"}
```

---

## 5. 关键实现说明

### 5.1 命名策略：全局 snake_case 双向映射

上游 FastAPI 的响应一律 snake_case（`table_name`、`row_count`、`chart_config`、
`analysis_steps`、`session_id`、`agent_ready`…）。

`application.yml` 中配置：

```yaml
spring:
  jackson:
    property-naming-strategy: SNAKE_CASE
```

`agent-pojo` 里的 VO 一律写 Java 驼峰字段名，由策略自动映射，
**不需要逐个写 `@JsonProperty`**，并保证网关对外的 JSON 与上游逐字段一致。

**踩坑记录（重要）**：仅靠 `spring.jackson` 配置**不足以保证** RestTemplate 反序列化正确 ——
实测 RestTemplate 的消息转换器列表里存在两个 JSON 转换器，其中一个是 `strategy=null`（未套用命名策略），
且会先匹配到响应类型，结果 `agent_ready`、`table_count` 等字段全部读成 `null`（只有单词字段如 `status` 正常）。

因此 `AgentApiClient` 采用更稳的做法：**统一先用 `String` 接收上游原始 JSON，
再用 `JsonUtils` 里显式配置了 `SNAKE_CASE` 的 `ObjectMapper` 自行反序列化**，
不依赖转换器的选择顺序。命名策略在代码里固定，无论 Spring 转换器如何变化都不会丢字段。

### 5.2 请求体：null 字段必须省略

上游 pydantic 模型把 `metadata`、`business_rules` 声明为**非 Optional 的字符串**。
若网关把它们序列化成 `"metadata": null`，pydantic 会判定类型不合法并返回 **HTTP 422**；
只有**缺省该字段**才会落到默认值 `""`。

因此三个请求 DTO 都加了 `@JsonInclude(JsonInclude.Include.NON_NULL)`。
同理，`AskRequest`/`RegressionRequest` 中带显式 alias 的字段（`sessionId`、`businessRules`、
`wantChart`、`wantReport`、`testSize`）用 `@JsonProperty` 强制写成 camelCase（显式 alias 优先级高于字段名），
并用 `@JsonAlias` 同时兼容 camelCase / snake_case 入参。

### 5.3 HTTP 客户端：连接池 + 超时

`RestTemplateConfig` 基于 **Apache HttpClient 5** 构建（Spring Framework 6 只支持 HttpClient 5，
构造器签名已改为 `org.apache.hc.client5.http.classic.HttpClient`）：

| 配置项 | 默认值 | 说明 |
|---|---|---|
| `agent.api.connect-timeout` | 10000ms | 连接超时（上游在本机，可快速失败） |
| `agent.api.read-timeout` | 180000ms | 响应超时，覆盖上游最长 90s 的同步问答 |
| `agent.api.connection-request-timeout` | 10000ms | 从连接池取连接的等待上限 |
| `agent.api.max-total-connections` | 200 | 连接池总量 |
| `agent.api.max-connections-per-route` | 50 | 单路由上限 |
| `agent.api.validate-after-inactivity-millis` | 2000 | 空闲连接复用前校验 |

同时：禁用自动重试（问答接口非幂等，重试可能导致重复执行）、空闲/过期连接回收、
`StringHttpMessageConverter` 默认字符集显式设为 UTF-8（避免中文乱码）。

另外关闭了 RestTemplate 默认的「非 2xx 抛异常」行为（`ResponseErrorHandler` 全部返回不处理），
这样上游 404/422 附带的 JSON body 才能被读取到并还原成可读的 `Result.error`。

### 5.4 SSE 流代理

`AgentSseService` 用 `SseEmitter` 代理上游事件流，关键设计：

- **不占用 Servlet 请求线程**：借用 RestTemplate 的连接池去打开上游流，读取与转发交给独立守护线程，
  避免高并发下 Tomcat 线程池被长达 10 分钟的流耗尽。
- **逐帧透传**：解析出上游的 `event:` 名与 `data:` 原文后原样重发，
  前端 `EventSource` 解析逻辑与直连 FastAPI 完全一致。
- **UTF-8 安全**：只在完整事件边界上做整体解码再切帧，
  规避「一次 read 把 UTF-8 汉字截成半个字符」导致的乱码。
- **超时**：10 分钟（与上游一致），到期自动关闭，避免连接泄漏。

### 5.5 上游地址可配置

上游基地址与前缀通过 `agent.api.base-url` / `agent.api.prefix` 注入
（`AgentApiProperties` + `@ConfigurationProperties`），**代码中不硬编码**，
便于切换测试 / 生产环境。

### 5.6 全局异常处理

`GlobalExceptionHandler`（`@RestControllerAdvice`）捕获：

| 异常 | 返回 |
|---|---|
| `BaseException`（业务异常 / 参数校验） | `Result.error(异常信息)`，WARN 日志 |
| `AgentApiException`（上游调用失败） | `Result.error(可读中文)`，ERROR 日志 |
| `HttpMessageNotReadableException` | `请求体不是合法 JSON 或缺少请求体` |
| `MethodArgumentNotValidException` | 字段级校验信息 |
| `MissingServletRequestParameterException` | `缺少必填参数：xxx` |
| `MethodArgumentTypeMismatchException` | `参数 xxx 类型不正确` |
| `HttpRequestMethodNotSupportedException` | `不支持的请求方法：xxx` |
| `NoHandlerFoundException` | `接口不存在：xxx` |
| `Exception`（兜底） | `服务内部异常：xxx`，完整堆栈只进日志 |

**保证网关永远返回统一信封，不向浏览器暴露 Java 堆栈。**

---

## 6. 与上游 FastAPI 的对接关系

| 维度 | 约定 |
|---|---|
| 基地址 | `http://127.0.0.1:8000`（配置项 `agent.api.base-url`） |
| 前缀 | `/api/v1`（配置项 `agent.api.prefix`） |
| 信封 | `{code, msg, data}`，`code=1` 成功 / `code=0` 失败 |
| 命名 | 网关对外与上游一致均为 **snake_case**；请求入参两种写法都接受 |
| 透传 | 网关**不二次包装**，前端只认一种契约 |
| 结构不固定字段 | `rows`、`sample_rows`、`chart_config.option`、`prompt_usage`、`coefficients`、`sample_predictions` 等一律用 `Map`/`List`/`JsonNode` 承接，不做强类型化，保证一个字段都不丢 |
| 只读约束 | 本模块**不修改 `agent/` 目录下的任何文件** |

**依赖方向**：Java 网关启动不依赖 FastAPI 在线，但在线前所有代理接口都会返回
`code=0` 与「Agent 服务不可用」提示。因此**排障顺序永远是：先确认 8000 通，再看 8080**。
`GET /api/v1/system/ping` 可在上游挂掉时确认 Java 网关自身是否存活。

---

## 7. 验证记录

以下为本机实测结果（JDK 21.0.9 / Spring Boot 3.2.3 / FastAPI 上游在线）：

| 验证项 | 命令 | 实际结果 |
|---|---|---|
| 编译打包 | `mvn -o -q package` | BUILD SUCCESS，三个模块均产出 jar |
| 启动 | `java -jar target\agent-server.jar` | Tomcat started on port 8080，profile=dev |
| 健康检查 | `GET /api/v1/system/health` | `code=1`，`agent_ready=true`、`table_count=3`、`llm_model=deepseek-chat`、`rag_enabled=true` |
| 元数据 | `GET /api/v1/metadata/tables` | `code=1`，**3 张表**；`fact_production_record` **45 列**、`row_count=15000` |
| snake_case | 同上原始 JSON | 键名为 `table_name`、`primary_key`、`time_column`、`row_count`、`standard_fields`、`sample_value`，且 `row_count` 为真实数值 `15000` |
| 知识图谱 | `GET /api/v1/knowledge/graph` | `code=1`，**24 个节点、22 条边**、5 个分类 |
| 建模字段 | `GET /api/v1/modeling/features?role=numeric` | `code=1`，`total=106` |
| 同步问答 | `POST /api/v1/agent/ask` | `code=1`，`task_type=sql_query`，`row_count=1`，SQL=`SELECT COUNT(*) AS record_count FROM fact_production_record`，`chart_config.chart_type=table`，`analysis_steps=5` |
| 异步任务 | `POST /api/v1/agent/ask/async` + `GET /jobs/{id}` | `code=1`，`job_id` 可用，`status/progress/stage` 正常推进 |
| SSE 代理 | `GET /jobs/{id}/stream` | `Content-Type: text/event-stream`，依次收到 `event:progress` 与 `event:done`（含完整 `result`） |
| 会话重置 | `DELETE /api/v1/agent/session/{id}` | `code=1`，`{"session_id":"...","reset":true}` |
| 异常检测 | `POST /api/v1/modeling/anomaly` | `code=1`，`algorithm=IsolationForest`，`total_records=1884`、`anomaly_count=95` |
| 回归建模 | `POST /api/v1/modeling/regression` | `code=1`，`algorithm=LinearRegression`，`r2_score=0.8019`、`rmse=0.3114`、`train_size=752`、`test_size=189` |
| CORS | `OPTIONS` + `Origin: http://localhost:5173` | HTTP 200，`Access-Control-Allow-Origin` 正确回显，`Allow-Credentials: true` |
| 上游宕机 | 停掉 FastAPI 后调用各接口 | HTTP 200 + `{"code":0,"msg":"Agent 服务不可用，请确认 FastAPI 已在 127.0.0.1:8000 启动"}`；SSE 推 `event:error` 后关闭 |

### 已知问题 / 待确认

1. **`POST /api/v1/agent/report` 返回的 `report` 为 `null`**：
   实测直连上游 `POST /api/v1/agent/report`（`wantReport=true`）同样返回
   `task_type=sql_query`、`report=null`。**这是上游既有行为，不是网关问题** ——
   网关已确认把 `wantReport=true` 正确转发（可通过 DEBUG 日志中的「上游请求体」核对）。
   接入报告功能前需先确认上游的报告生成链路。
2. **`agent-pojo` 中的 `TableMetadataVO` / `ColumnMetadataVO` 目前未被业务代码使用**：
   `AgentApiClient` 对元数据接口刻意返回 `Map<String,Object>`（`data` 含
   `relationships`/`field_map`/`metric_inventory` 等结构不固定字段，
   用 Map 才能保证原样透传）。这两个 VO 作为字段结构的可读文档与强类型调用入口保留。
3. **`/api/v1/agent/ask` 同步接口最长可能阻塞 180 秒**：上游自身最长 90 秒，
   网关读取超时设为 180 秒留足余量。生产环境建议前端优先使用
   `ask/async` + SSE，避免长时间占用浏览器连接。
4. **`metadata/relationships` 返回空数组**：上游说明当前数据模型是单表宽表
   （`dim_*` 维表已删除），关系图请改用 `/api/v1/knowledge/graph`。网关如实透传。

---

## 8. 常见问题排查

| 现象 | 原因与处理 |
|---|---|
| 所有接口返回 `Agent 服务不可用` | FastAPI 未启动或不在 8000。先 `curl http://127.0.0.1:8000/api/v1/system/health` |
| 接口返回 `上游 Agent 校验请求参数失败（HTTP 422）` | 请求体字段类型与上游不符。注意可选字段不要传 `null`（应缺省） |
| 字段全是 `null`（如 `agent_ready`） | 命名策略未生效。已通过 `JsonUtils` 显式固定 `SNAKE_CASE` 解决；若二次开发请勿改为强类型直收 |
| 中文乱码 | 确认启动参数含 `"-Dfile.encoding=UTF-8"`（注意 PowerShell 需加引号） |
| 打包报 `Unable to rename ... agent-server.jar` | jar 正被运行中的 JVM 占用，先停掉再打包 |
| `mvn spring-boot:run` 失败 | 离线仓库缺该插件依赖，请改用 `java -jar target\agent-server.jar` |
| PowerShell 报 `Unknown lifecycle phase ".test.skip=true"` | PowerShell 会按 `.` 切分未加引号的 `-D` 参数，需写成 `"-Dmaven.test.skip=true"` |
