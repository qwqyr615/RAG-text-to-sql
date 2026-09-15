@echo off
REM 停止本系统的三层服务（保留 MySQL / Milvus）
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0stop-all.ps1"
pause
