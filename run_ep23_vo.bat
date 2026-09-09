@echo off
cd /d c:/Users/michael/CodeBuddy/ai_movie_agent
echo SERIES_START > outputs/series_ep23.log
python run_series.py --ep 2 >> outputs/series_ep23.log 2>&1
if errorlevel 1 (echo EP2_FILM_FAIL >> outputs/series_ep23.log) else (echo EP2_FILM_OK >> outputs/series_ep23.log)
set HTTPS_PROXY=
set HTTP_PROXY=
python make_narration.py --series-script outputs/series_script.json --ep 2 --film outputs/ep2_series_film.mp4 --out outputs/ep2_series_vo.mp4 --auto-dur >> outputs/series_ep23.log 2>&1
if errorlevel 1 (echo EP2_VO_FAIL >> outputs/series_ep23.log) else (echo EP2_VO_DONE >> outputs/series_ep23.log)
python run_series.py --ep 3 >> outputs/series_ep23.log 2>&1
if errorlevel 1 (echo EP3_FILM_FAIL >> outputs/series_ep23.log) else (echo EP3_FILM_OK >> outputs/series_ep23.log)
python make_narration.py --series-script outputs/series_script.json --ep 3 --film outputs/ep3_series_film.mp4 --out outputs/ep3_series_vo.mp4 --auto-dur >> outputs/series_ep23.log 2>&1
if errorlevel 1 (echo EP3_VO_FAIL >> outputs/series_ep23.log) else (echo EP3_VO_DONE >> outputs/series_ep23.log)
echo ALL_DONE >> outputs/series_ep23.log
