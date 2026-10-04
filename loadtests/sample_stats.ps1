# Samples docker stats every ~2 s into a CSV until the stop file exists.
# Usage: powershell -File loadtests/sample_stats.ps1 -Out loadtests/results/platform-stats.csv -Containers sp-loadtest-fake,svoipravila-postgres-1,svoipravila-redis-1
param(
	[string]$Out = "loadtests/results/stats.csv",
	[string]$Containers = "sp-loadtest-fake,svoipravila-postgres-1,svoipravila-redis-1",
	[string]$StopFile = "loadtests/results/stop.flag",
	[int]$MaxSeconds = 900
)
"utc,name,cpu_pct,mem_usage" | Set-Content -Encoding utf8 $Out
$names = $Containers -split ','
$deadline = (Get-Date).AddSeconds($MaxSeconds)
while (-not (Test-Path $StopFile) -and (Get-Date) -lt $deadline) {
	$ts = (Get-Date).ToUniversalTime().ToString('yyyy-MM-ddTHH:mm:ssZ')
	$lines = docker stats --no-stream --format "{{.Name}},{{.CPUPerc}},{{.MemUsage}}" @names
	foreach ($line in $lines) { "$ts,$line" | Add-Content -Encoding utf8 $Out }
}
