@echo off
REM 全自动投稿：未登录则先扫码登录；登录成功后自动投稿 demo 到 B 站。
REM 增强：投稿失败自动重试（最多 3 次）；投稿成功自动打开 B 站稿件页。
REM 唯一需要你做的是扫码（biliup 只能在真实终端显示二维码，无法后台自动化）。
cd /d "C:\Users\michael\CodeBuddy\20260825154207\ai_movie_agent\outputs"
if not exist cookies.json (
    echo [全自动] 未检测到登录，启动 B 站扫码登录（用 App 扫码后窗口会自动继续）...
    "C:\Users\michael\CodeBuddy\20260825154207\ai_movie_agent\tools\biliup\biliupR-v0.2.4-x86_64-windows\biliup.exe" login
)
cd /d "C:\Users\michael\CodeBuddy\20260825154207\ai_movie_agent"

set RETRY=1
:retry
echo [全自动] 第 %RETRY% 次尝试投稿 demo 视频...
python cli.py publish-concept --submit --bgm > outputs\publish_attempt.log 2>&1
findstr /C:"STATUS=OK" outputs\publish_attempt.log >nul
if %errorlevel%==0 (
    echo [全自动] 投稿成功！正在打开 B 站稿件页...
    type outputs\publish_attempt.log
    start "" "https://member.bilibili.com/platform/upload-manager/article"
    goto done
)
if %RETRY%==3 (
    echo [全自动] 已重试 3 次仍失败，请查看日志 outputs\publish_attempt.log
    type outputs\publish_attempt.log
    goto done
)
echo [全自动] 投稿失败，3 秒后重试（第 %RETRY% 次）...
type outputs\publish_attempt.log
timeout /t 3 >nul
set /a RETRY+=1
goto retry
:done
pause
