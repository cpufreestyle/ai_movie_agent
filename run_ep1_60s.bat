@echo off
cd /d c:\Users\michael\CodeBuddy\ai_movie_agent
setlocal
set PY=c:\venv_h3\Scripts\python.exe
set LOG=outputs\ep1_60s.log
echo [%date% %time%] START render ep1 90f (mmh3) >> %LOG%
%PY% run_series.py --engine mmh3 --ep 1 --frames 90 >> %LOG% 2>&1
if errorlevel 1 (
  echo [%date% %time%] RENDER FAILED >> %LOG%
  goto :eof
)
echo [%date% %time%] render OK, START vo (fit 1.0x) >> %LOG%
%PY% make_narration.py --film outputs/ep1_series_film_mmh3.mp4 --out outputs/ep1_vo_mmh3.mp4 --series-script outputs/series_script.json --ep 1 --fit-film --fps 24 --auto-dur --orig-vol 0.18 >> %LOG% 2>&1
echo [%date% %time%] DONE vo >> %LOG%
