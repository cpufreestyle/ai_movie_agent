@echo off
set "TN=ai_movie_agent_supervise"
schtasks /delete /tn "%TN%" /f
if %errorlevel%==0 (
  echo [OK] 已移除自启任务：%TN%
) else (
  echo [INFO] 未找到该任务或删除失败（errorlevel=%errorlevel%）。
)
pause
