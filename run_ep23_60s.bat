@echo off
cd /d c:\Users\michael\CodeBuddy\ai_movie_agent
setlocal
set PY=c:\venv_h3\Scripts\python.exe
set LOG=outputs\ep23_60s.log
rem 等第1集后台作业(PID 36336)结束，避免与同一 manifest 并发写冲突
:wait
tasklist /fi "PID eq 36336" | find "36336" >nul
if not errorlevel 1 (
  echo [%date% %time%] 等待 ep1 作业结束... >> %LOG%
  timeout /t 30 >nul
  goto wait
)
echo [%date% %time%] ep1 作业已结束，开始 ep2/ep3 >> %LOG%

echo [%date% %time%] START ep2 render >> %LOG%
%PY% run_series.py --engine mmh3 --ep 2 --frames 90 >> %LOG% 2>&1
if errorlevel 1 ( echo [%date% %time%] EP2 RENDER FAILED >> %LOG% & goto :ep3 )
echo [%date% %time%] ep2 render OK, START vo >> %LOG%
%PY% make_narration.py --film outputs/ep2_series_film_mmh3.mp4 --out outputs/ep2_vo_mmh3.mp4 --series-script outputs/series_script.json --ep 2 --fit-film --fps 24 --auto-dur --orig-vol 0.18 >> %LOG% 2>&1
echo [%date% %time%] ep2 DONE >> %LOG%

:ep3
echo [%date% %time%] START ep3 render >> %LOG%
%PY% run_series.py --engine mmh3 --ep 3 --frames 90 >> %LOG% 2>&1
if errorlevel 1 ( echo [%date% %time%] EP3 RENDER FAILED >> %LOG% & goto :eof )
echo [%date% %time%] ep3 render OK, START vo >> %LOG%
%PY% make_narration.py --film outputs/ep3_series_film_mmh3.mp4 --out outputs/ep3_vo_mmh3.mp4 --series-script outputs/series_script.json --ep 3 --fit-film --fps 24 --auto-dur --orig-vol 0.18 >> %LOG% 2>&1
echo [%date% %time%] ep3 DONE >> %LOG%
