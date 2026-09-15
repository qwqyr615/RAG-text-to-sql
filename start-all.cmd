@echo off
REM 一键启动：双击即可。默认前端用 dev 模式（热更新）。
REM 如需生产构建模式：start-all.cmd preview
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0start-all.ps1" %*
pause
