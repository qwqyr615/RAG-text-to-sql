@echo off
REM Stop the three services of this system (MySQL / Milvus are left running).
REM NOTE: keep this file ASCII-only - see the note in start-all.cmd.
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0stop-all.ps1"
pause
