# ======================================================================
# 停止本系统的三层服务（只停本系统进程，不动 MySQL / Milvus）
#
# 用法（在仓库根目录）：
#   powershell -ExecutionPolicy Bypass -File .\stop-all.ps1
#   或双击 stop-all.cmd
# ======================================================================

$ErrorActionPreference = 'Continue'

# 注意：不要用 $pid 作变量名 —— 它是 PowerShell 的只读自动变量
function Stop-ServicePort($port, $name) {
    $connections = Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction SilentlyContinue
    if (-not $connections) {
        Write-Host "  $name ($port) 未在运行" -ForegroundColor DarkGray
        return
    }
    $ownerIds = $connections | Select-Object -ExpandProperty OwningProcess -Unique
    foreach ($ownerId in $ownerIds) {
        try {
            $proc = Get-Process -Id $ownerId -ErrorAction Stop
            Write-Host "  停止 $name ($port) -> PID $ownerId ($($proc.ProcessName))" -ForegroundColor Yellow
            Stop-Process -Id $ownerId -Force -ErrorAction Stop
        }
        catch {
            Write-Host "  无法停止 PID $ownerId ：$($_.Exception.Message)" -ForegroundColor Red
        }
    }
}

Write-Host ''
Write-Host '=== 停止本系统服务 ===' -ForegroundColor Cyan
Stop-ServicePort -port 5173 -name 'React 前端'
Stop-ServicePort -port 8080 -name 'Java 网关'
Stop-ServicePort -port 8000 -name 'FastAPI'

Start-Sleep -Seconds 2

Write-Host ''
Write-Host '=== 结果 ===' -ForegroundColor Cyan
$targets = @(
    [pscustomobject]@{ Port = 5173; Name = '前端' },
    [pscustomobject]@{ Port = 8080; Name = 'Java 网关' },
    [pscustomobject]@{ Port = 8000; Name = 'FastAPI' }
)
foreach ($target in $targets) {
    $still = Get-NetTCPConnection -LocalPort $target.Port -State Listen -ErrorAction SilentlyContinue
    if ($still) {
        Write-Host "  $($target.Name) ($($target.Port)) 仍在监听" -ForegroundColor Red
    }
    else {
        Write-Host "  $($target.Name) ($($target.Port)) 已停止" -ForegroundColor Green
    }
}

Write-Host ''
Write-Host '注意：MySQL(3306) 与 Milvus(19530) 未被停止，如需停止请手动操作。' -ForegroundColor DarkGray
Write-Host ''
