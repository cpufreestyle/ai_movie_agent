$py = (Resolve-Path 'd:\ai sheare\repo\ai*\.venv\Scripts\python.exe').Path
$env:HTTP_PROXY='http://127.0.0.1:7897'
$env:HTTPS_PROXY='http://127.0.0.1:7897'
Set-Location 'c:\Users\michael\CodeBuddy\ai_movie_agent'
Write-Host "=== 重跑旁白+字幕(放大字号+限速+精简台词) py=$py ==="
& $py make_narration.py --film outputs/ep1_up_mmh3.mp4 --out outputs/ep1_vo_up_mmh3.mp4 --fit-film --fps 24 --shots 18 --lines-json outputs/ep1_nar_slow.json
if (-not (Test-Path 'outputs/ep1_vo_up_mmh3.mp4')) { Write-Host 'NAR2_FAILED'; exit 1 }
Write-Host 'NAR2_DONE -> outputs/ep1_vo_up_mmh3.mp4'
