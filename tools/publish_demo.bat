@echo off
REM 投稿 demo 视频到 B 站（需先运行 login.bat 登录生成 cookies.json）
cd /d "C:\Users\michael\CodeBuddy\20260825154207\ai_movie_agent"
python cli.py publish-concept --submit
pause
