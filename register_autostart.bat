@echo off
rem 注册「登录自启」计划任务：每次登录自动跑 run_guard.bat（=> supervisor => 两条服务）。
rem 用户级任务，一般无需管理员；如报权限错误，右键「以管理员身份运行」再试。
cd /d "%~dp0"
set "TN=ai_movie_agent_supervise"
schtasks /create /tn "%TN%" /tr "%~dp0run_guard.bat" /sc onlogon /rl LIMITED /f
if %errorlevel%==0 (
  echo [OK] 已注册登录自启任务：%TN%
) else (
  echo [FAIL] 注册失败（errorlevel=%errorlevel%）—— 可能需要管理员权限。
)
echo.
schtasks /query /tn "%TN%" /fo LIST 2>nul
pause
