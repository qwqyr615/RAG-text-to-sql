# ======================================================================
# 企业数据底座智能问析 Agent 系统 —— 一键启动脚本
#
# 依次启动三层，每一步都等待服务真正就绪后才继续：
#   1. FastAPI 适配层   (:8000)  ← Agent 内核
#   2. Spring Boot 网关 (:8080)  ← 转发 + 统一信封
#   3. React 前端       (:5173)  ← 用户界面
#
# 用法（在仓库根目录）：
#   powershell -ExecutionPolicy Bypass -File .\start-all.ps1
#   或双击 start-all.cmd
#
# 参数：
#   -FrontendMode dev      前端用开发模式（热更新，首次打开较慢）—— 默认
#   -FrontendMode preview  前端用生产构建（需先 build，打开更快）
#   -SkipDependencyCheck   跳过 MySQL / Milvus 连通性检查
#
# 前置依赖：MySQL 必须已启动（见 README「MySQL 需要管理员权限启动」一节）。
# ======================================================================

[CmdletBinding()]
param(
    [ValidateSet('dev', 'preview')]
    [string]$FrontendMode = 'dev',
    [switch]$SkipDependencyCheck
)

$ErrorActionPreference = 'Stop'
$Root = $PSScriptRoot

# ---- 本机环境（换机器时改这三项） ----
$PythonExe = 'D:\Anaconda\envs\sqllangchain\python.exe'
$JavaHome = 'E:\develop\jdk'          # 本机只有 JDK 21（无 JDK 17）
$JavaExe = Join-Path $JavaHome 'bin\java.exe'
$MavenExe = 'D:\develop\apache-maven-3.9.11\bin\mvn.cmd'

function Write-Step($text) { Write-Host '' ; Write-Host "=== $text ===" -ForegroundColor Cyan }
function Write-Ok($text) { Write-Host "  [OK]   $text" -ForegroundColor Green }
function Write-Warn2($text) { Write-Host "  [警告] $text" -ForegroundColor Yellow }
function Write-Err($text) { Write-Host "  [错误] $text" -ForegroundColor Red }

function Test-Port($port) {
    return [bool](Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction SilentlyContinue)
}

# 轮询等待 HTTP 接口可用：端口开了不代表内核就绪，必须看接口返回
function Wait-Http($url, $name, $timeoutSec) {
    $deadline = (Get-Date).AddSeconds($timeoutSec)
    while ((Get-Date) -lt $deadline) {
        try {
            $response = Invoke-RestMethod -Uri $url -TimeoutSec 10
            if ($null -ne $response -and $response.code -eq 1) {
                Write-Ok "$name 已就绪"
                return $true
            }
        }
        catch {
            # 未就绪，继续等
        }
        Start-Sleep -Seconds 2
    }
    Write-Err "$name 在 $timeoutSec 秒内未就绪"
    return $false
}

# 按 UTF-8 解码上游返回的 JSON。
#
# 背景：FastAPI 的响应头是 application/json 且不带 charset，而 Windows PowerShell 5.1
# 在这种情况下会把响应体按 ISO-8859-1 解码，于是上游的中文全变乱码
# （"生产记录" -> "çæäº§è®°å½•"，即 UTF-8 字节被逐个当成 Latin-1 字符）。
# 脚本自身的中文是正常的，控制台编码也没问题，问题只出在这一次解码上，
# 所以这里自己取原始字节再按 UTF-8 解码。
function Get-JsonUtf8($url, $timeoutSec = 10) {
    $response = Invoke-WebRequest -Uri $url -TimeoutSec $timeoutSec -UseBasicParsing
    $bytes = $null
    try { $bytes = $response.RawContentStream.ToArray() } catch { }
    if ($bytes -and $bytes.Length -gt 0) {
        return ([System.Text.Encoding]::UTF8.GetString($bytes) | ConvertFrom-Json)
    }
    # 兜底：PowerShell 7 的 .Content 本身就是按 UTF-8 解码的
    return ($response.Content | ConvertFrom-Json)
}

Write-Host ''
Write-Host '==========================================================' -ForegroundColor White
Write-Host '  企业数据底座智能问析 Agent 系统' -ForegroundColor White
Write-Host '  启动顺序：FastAPI(8000) -> Java网关(8080) -> 前端(5173)' -ForegroundColor White
Write-Host '==========================================================' -ForegroundColor White

# ---- 0. 环境检查 ----
Write-Step '0/4 检查运行环境'
$toolList = @(
    [pscustomobject]@{ Path = $PythonExe; Name = 'Python 解释器' },
    [pscustomobject]@{ Path = $JavaExe; Name = 'JDK' },
    [pscustomobject]@{ Path = $MavenExe; Name = 'Maven' }
)
foreach ($tool in $toolList) {
    if (Test-Path $tool.Path) {
        Write-Ok "$($tool.Name)：$($tool.Path)"
    }
    else {
        Write-Err "$($tool.Name) 不存在：$($tool.Path)"
        Write-Host '  请修改本脚本顶部的路径配置。' -ForegroundColor DarkGray
        exit 1
    }
}
if (-not (Test-Path (Join-Path $Root 'FRONTEND\node_modules'))) {
    Write-Err '前端依赖未安装。请先执行：cd FRONTEND; pnpm install'
    exit 1
}

# ---- 1. 依赖服务 ----
if (-not $SkipDependencyCheck) {
    Write-Step '1/4 检查依赖服务'
    $deps = @(
        [pscustomobject]@{ Port = 3306; Name = 'MySQL（业务数据源）' },
        [pscustomobject]@{ Port = 19530; Name = 'Milvus（RAG 向量库）' }
    )
    foreach ($dep in $deps) {
        if (Test-Port $dep.Port) {
            Write-Ok "$($dep.Name) 已监听 $($dep.Port)"
        }
        else {
            Write-Warn2 "$($dep.Name) 未监听 $($dep.Port)"
        }
    }
    if (-not (Test-Port 3306)) {
        Write-Host ''
        Write-Err 'MySQL 未启动，Agent 内核将无法就绪（/system/health 会返回 agent_ready=false）。'
        Write-Host '  MySQL 以 Windows 服务方式安装，启动需要管理员权限：' -ForegroundColor DarkGray
        Write-Host '    以【管理员身份】打开 PowerShell，执行：' -ForegroundColor DarkGray
        Write-Host '      Start-Service MySQL80' -ForegroundColor White
        Write-Host '  然后再运行本脚本。' -ForegroundColor DarkGray
        Write-Host '  （Milvus 未启动只会导致 RAG 示例检索失效，不影响问答主流程）' -ForegroundColor DarkGray
        exit 1
    }
}

# ---- 2. FastAPI ----
Write-Step '2/4 启动 FastAPI 适配层 (:8000)'
if (Test-Port 8000) {
    Write-Warn2 '8000 已被占用，跳过启动（若为旧进程，请先运行 stop-all.ps1，否则会加载旧代码）'
}
else {
    $uvicornArgs = @('-m', 'uvicorn', 'server.main:app', '--host', '127.0.0.1', '--port', '8000')
    Start-Process -FilePath $PythonExe -ArgumentList $uvicornArgs `
        -WorkingDirectory (Join-Path $Root 'agent') -WindowStyle Minimized
    Write-Host '  已在新窗口启动 uvicorn（首次启动要读取元数据，约 10~20 秒）'
}
if (-not (Wait-Http 'http://127.0.0.1:8000/api/v1/system/health' 'FastAPI' 240)) {
    Write-Err 'FastAPI 启动失败。请查看刚弹出的窗口里的错误信息。'
    exit 1
}
$health = Get-JsonUtf8 -url 'http://127.0.0.1:8000/api/v1/system/health' -timeoutSec 10
if ($health.data.agent_ready) {
    Write-Host ("  数据库={0}  业务表={1} 张  模型={2}  RAG={3}" -f `
            $health.data.database_type, $health.data.table_count, $health.data.llm_model, $health.data.rag_enabled)
}
else {
    Write-Warn2 "内核未就绪：$($health.data.error)"
    Write-Warn2 '服务仍会启动，但问答/建模接口会失败。请先修复上述问题。'
}

# ---- 3. Java 网关 ----
Write-Step '3/4 启动 Spring Boot 网关 (:8080)'
$jar = Join-Path $Root 'backend\agent-server\target\agent-server.jar'
if (-not (Test-Path $jar)) {
    Write-Warn2 '未找到 agent-server.jar，正在构建（离线模式，约 10 秒）…'
    & $MavenExe -o -q -f (Join-Path $Root 'backend\pom.xml') package
    if ($LASTEXITCODE -ne 0) {
        Write-Err '后端构建失败，请手动执行 mvn -o package 查看原因'
        exit 1
    }
    Write-Ok '后端构建完成'
}
if (Test-Port 8080) {
    Write-Warn2 '8080 已被占用，跳过启动'
}
else {
    # 注意：Start-Process 的 -ArgumentList 只是把数组用空格拼成命令行，不会自动加引号。
    # $jar 是含空格的路径时（例如仓库目录叫「企业数据底座智能问析 Agent 系统」），
    # 必须手动加引号，否则 java 收到被空格截断的路径后立刻退出，
    # 脚本会白等 120 秒再报「Java 网关启动失败」，看起来就像卡在 java 层：
    #   Error: Unable to access jarfile E:\code\Agent\企业数据底座智能问析
    Start-Process -FilePath $JavaExe -ArgumentList @('-jar', "`"$jar`"") `
        -WorkingDirectory (Join-Path $Root 'backend\agent-server') -WindowStyle Minimized
    Write-Host '  已在新窗口启动 Java 网关（约 5 秒）'
}
if (-not (Wait-Http 'http://127.0.0.1:8080/api/v1/system/health' 'Java 网关' 120)) {
    Write-Err 'Java 网关启动失败。请查看其窗口日志。'
    exit 1
}

# ---- 4. 前端 ----
Write-Step "4/4 启动 React 前端 (:5173，模式=$FrontendMode)"
if (Test-Port 5173) {
    Write-Warn2 '5173 已被占用，跳过启动（切换 dev/preview 前请先运行 stop-all.ps1）'
}
else {
    if ($FrontendMode -eq 'preview') {
        if (-not (Test-Path (Join-Path $Root 'FRONTEND\dist\index.html'))) {
            Write-Warn2 'dist 不存在，先执行生产构建…'
            & pnpm --dir (Join-Path $Root 'FRONTEND') build
        }
        $viteArgs = @('exec', 'vite', 'preview')
    }
    else {
        $viteArgs = @('exec', 'vite')
    }
    Start-Process -FilePath 'pnpm.cmd' -ArgumentList $viteArgs `
        -WorkingDirectory (Join-Path $Root 'FRONTEND') -WindowStyle Minimized
    Write-Host "  已在新窗口启动前端（$FrontendMode 模式）"
}
Start-Sleep -Seconds 6

# ---- 汇总 ----
$gatewayOk = $false
try {
    $gatewayOk = (Invoke-RestMethod -Uri 'http://127.0.0.1:8080/api/v1/system/health' -TimeoutSec 10).code -eq 1
}
catch {
    $gatewayOk = $false
}

Write-Host ''
Write-Host '==========================================================' -ForegroundColor White
Write-Host '  启动完成' -ForegroundColor Green
Write-Host '----------------------------------------------------------' -ForegroundColor White
Write-Host '  前端界面   : http://127.0.0.1:5173'
Write-Host '  Java 网关  : http://127.0.0.1:8080/api/v1/system/health'
Write-Host '  FastAPI    : http://127.0.0.1:8000/docs   (Swagger 文档)'
Write-Host ("  网关状态   : {0}" -f $(if ($gatewayOk) { '正常' } else { '异常' }))
Write-Host '----------------------------------------------------------' -ForegroundColor White
Write-Host '  停止服务   : .\stop-all.ps1'
Write-Host '==========================================================' -ForegroundColor White
Write-Host ''
