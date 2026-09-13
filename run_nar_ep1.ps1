$py='d:\ai sheare\repo\ai管理\.venv\Scripts\python.exe'
$env:HTTP_PROXY='http://127.0.0.1:7897'
$env:HTTPS_PROXY='http://127.0.0.1:7897'
Set-Location 'c:\Users\michael\CodeBuddy\ai_movie_agent'
$deadline=(Get-Date).AddMinutes(60)
$done=$false
while ((Get-Date) -lt $deadline) {
  if (Test-Path 'outputs/_up_ep1_full.log') {
    $done = Select-String -Path 'outputs/_up_ep1_full.log' -Pattern 'done outputs' -Quiet -ErrorAction SilentlyContinue
  }
  if ($done) { break }
  Start-Sleep -Seconds 30
}
if (-not $done) { Write-Host 'TIMEOUT waiting for upscale'; exit 1 }
Write-Host 'Upscale finished, generating narration + bilingual subtitles...'
& $py make_narration.py --film outputs/ep1_up_mmh3.mp4 --out outputs/ep1_vo_up_mmh3.mp4 --fit-film --fps 24 --series-script outputs/series_script.json --ep 1
Write-Host 'NAR_DONE'
