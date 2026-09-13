$py='c:\venv_h3\Scripts\python.exe'
$py2='d:\ai sheare\repo\ai管理\.venv\Scripts\python.exe'
Set-Location 'c:\Users\michael\CodeBuddy\ai_movie_agent'

# 清理：杀掉任何残留 run_series 进程，避免两个进程抢同一个 manifest / 镜头文件
Get-CimInstance Win32_Process -Filter "Name='python.exe'" | Where-Object { $_.CommandLine -like '*run_series*' } | ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }
Start-Sleep -Seconds 5
Remove-Item 'outputs/series_shots_mmh3/ep1_shot*.mp4' -Force -ErrorAction SilentlyContinue
Write-Host "[clean] old run_series killed, ep1 shots cleared"

Write-Host "[1/2] render 60s H3 1024x576 ep1 (--force)"
& $py run_series.py --engine mmh3 --ep 1 --frames 90 --width 1024 --height 576 --force
if ($LASTEXITCODE -ne 0) { Write-Host "RENDER FAILED rc=$LASTEXITCODE"; exit $LASTEXITCODE }

Write-Host "[2/2] narration + bilingual subs (no upscale)"
& $py2 make_narration.py --film outputs/ep1_series_film_mmh3.mp4 --out outputs/ep1_vo_mmh3.mp4 --fit-film --auto-dur --fps 24 --series-script outputs/series_script.json --ep 1 --no-upscale
Write-Host "ALL DONE rc=$LASTEXITCODE"
