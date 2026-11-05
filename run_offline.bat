@echo off
chcp 65001 >nul
cd /d "%~dp0"
set "PY=python"
if not "%CIVENTHRA_PYTHON%"=="" set "PY=%CIVENTHRA_PYTHON%"
set PYTHONUTF8=1

echo.
echo  ============================================
echo   CIVENTHRA BOX  -  local-first mode
echo  ============================================
echo.
echo  [1] Web UI   (recommended, 含语音对话)
echo  [2] Terminal chat
echo  [3] Demo (three acts)
echo  [4] Rebuild index
echo  [5] Build embedding index (needs net once)
echo  [6] 语音服务自检 / 单独启动
echo.
set /p CH=choose 1-6 [1]: 
if "%CH%"=="" set CH=1

if "%CH%"=="1" "%PY%" -X utf8 -u -m civenthra.serve
if "%CH%"=="2" "%PY%" -X utf8 -u -m civenthra.cli
if "%CH%"=="3" "%PY%" -X utf8 -u -m civenthra.demo
if "%CH%"=="4" "%PY%" -X utf8 -u -m civenthra.ingest
if "%CH%"=="5" "%PY%" -X utf8 -u build_dense.py
if "%CH%"=="6" call "启动语音服务.bat"

echo.
pause
