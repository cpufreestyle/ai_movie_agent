@echo off
REM 一键登录 B 站（需真实终端，扫码后自动生成 outputs\cookies.json）
cd /d "C:\Users\michael\CodeBuddy\20260825154207\ai_movie_agent\outputs"
"C:\Users\michael\CodeBuddy\20260825154207\ai_movie_agent\tools\biliup\biliupR-v0.2.4-x86_64-windows\biliup.exe" login
pause
