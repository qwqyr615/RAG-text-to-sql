@echo off
REM One-click launcher: just double-click. Frontend defaults to dev mode (HMR).
REM Production build mode: start-all.cmd preview
REM
REM NOTE: keep this file ASCII-only.
REM It used to carry Chinese REM comments here, but git stores this file with LF
REM line endings; cmd.exe parses .cmd files using the OEM codepage (936/GBK on a
REM Chinese Windows), mis-splits those multi-byte comments, and then tries to run
REM fragments of the comment text as commands:
REM   '?dev' is not recognized as an internal or external command,
REM   or operable program or batch file.
REM The Chinese help text lives in start-all.ps1, which is UTF-8 WITH BOM and is
REM read correctly by Windows PowerShell 5.1.
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0start-all.ps1" %*
pause
