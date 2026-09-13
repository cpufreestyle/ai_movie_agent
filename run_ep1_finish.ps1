$py = (Resolve-Path 'd:\ai sheare\repo\ai*\.venv\Scripts\python.exe').Path
$env:HTTP_PROXY='http://127.0.0.1:7897'
$env:HTTPS_PROXY='http://127.0.0.1:7897'
Set-Location 'c:\Users\michael\CodeBuddy\ai_movie_agent'

Write-Host "=== [1/2] 续跑超分(跳过已完成分段, 去锐化防OOM) py=$py ==="
& $py upscale_video.py outputs/ep1_series_film_mmh3.mp4 outputs/ep1_up_mmh3.mp4 --full --chunk 60 --crf 17 --sharpen 0
if (-not (Test-Path 'outputs/ep1_up_mmh3.mp4')) { Write-Host 'UPSCALE_FAILED'; exit 1 }
Write-Host "=== 超分完成: outputs/ep1_up_mmh3.mp4 ==="

Write-Host "=== [2/2] 加英文旁白 + 中英双语字幕 ==="
& $py make_narration.py --film outputs/ep1_up_mmh3.mp4 --out outputs/ep1_vo_up_mmh3.mp4 --fit-film --fps 24 --series-script outputs/series_script.json --ep 1
if (-not (Test-Path 'outputs/ep1_vo_up_mmh3.mp4')) { Write-Host 'NAR_FAILED'; exit 1 }
Write-Host 'ALL_DONE -> outputs/ep1_vo_up_mmh3.mp4'
