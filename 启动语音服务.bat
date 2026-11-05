@echo off
chcp 65001 >nul
cd /d "%~dp0"
rem ════════════════════════════════════════════════════════════════════
rem  文明穹顶 · 语音服务（voicebox）
rem
rem  平时**不需要单独跑这个**：主程序（civenthra.serve）第一次收到语音
rem  请求时会自动把它拉起来。这个脚本是给排障用的。
rem
rem  默认使用 PATH 中的 Python；可用 CIVENTHRA_VOICE_PYTHON 指定独立环境。
rem ════════════════════════════════════════════════════════════════════

set "PY=%CIVENTHRA_VOICE_PYTHON%"
if not defined PY set "PY=python"
set PYTHONUTF8=1

echo.
echo  ============================================
echo   CIVENTHRA VOICEBOX   (127.0.0.1:8790)
echo  ============================================
echo.
echo  [1] 起服务（前台）
echo  [2] 自检：合成一段话 → 再识别回来（约 15 秒，会载模型）
echo  [3] 查看服务状态
echo.
set /p CH=choose 1-3 [1]:
if "%CH%"=="" set CH=1

if "%CH%"=="1" if not defined CIVENTHRA_LOCAL_API_TOKEN (
  echo  缺少 CIVENTHRA_LOCAL_API_TOKEN。请直接启动 civenthra.serve；它会生成临时令牌并自动拉起语音服务。
  goto done
)
if "%CH%"=="1" "%PY%" -X utf8 -u voicebox.py --port 8790
if "%CH%"=="2" "%PY%" -X utf8 -u voicebox.py --selftest
if "%CH%"=="3" if not defined CIVENTHRA_LOCAL_API_TOKEN (
  echo  缺少 CIVENTHRA_LOCAL_API_TOKEN，无法查询受保护的服务。
  goto done
)
if "%CH%"=="3" "%PY%" -X utf8 -u -c "import json,os,urllib.request as u;r=u.Request('http://127.0.0.1:8790/health',headers={'X-Civenthra-Token':os.environ['CIVENTHRA_LOCAL_API_TOKEN']});print(json.dumps(json.loads(u.urlopen(r,timeout=4).read()),ensure_ascii=False,indent=2))"

:done
echo.
pause
