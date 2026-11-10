# ArcaNote - installa l'IA locale (Ollama + modello) e misura la velocita' del PC.
$ErrorActionPreference = "Stop"
$modello = "qwen2.5:7b"
function Say($m) { Write-Host ""; Write-Host $m -ForegroundColor Cyan }

Say "1/4 Controllo scheda grafica"
try { nvidia-smi --query-gpu=name,memory.total --format=csv,noheader }
catch { Write-Host "Scheda NVIDIA non rilevata (driver mancante?): l'IA girera' sul processore, molto piu' lenta." -ForegroundColor Yellow }

Say "2/4 Installazione di Ollama"
function Refresh-Path { $env:Path = [Environment]::GetEnvironmentVariable("Path","Machine") + ";" + [Environment]::GetEnvironmentVariable("Path","User") }
if (-not (Get-Command ollama -ErrorAction SilentlyContinue)) {
    if (-not (Get-Command winget -ErrorAction SilentlyContinue)) { throw "winget non trovato. Installa Ollama da https://ollama.com/download e rilancia lo script." }
    winget install -e --id Ollama.Ollama --accept-package-agreements --accept-source-agreements
    Refresh-Path
    if (-not (Get-Command ollama -ErrorAction SilentlyContinue)) { $env:Path += ";$env:LOCALAPPDATA\Programs\Ollama" }
}
$up = $false
for ($i = 0; $i -lt 20 -and -not $up; $i++) {
    try { Invoke-RestMethod http://localhost:11434/api/tags -TimeoutSec 3 | Out-Null; $up = $true }
    catch { if ($i -eq 0) { Start-Process ollama -ArgumentList "serve" -WindowStyle Hidden }; Start-Sleep 2 }
}
if (-not $up) { throw "Ollama non risponde su localhost:11434." }

Say "3/4 Download del modello $modello (circa 5 GB, solo la prima volta)"
ollama pull $modello

Say "4/4 Prova di velocita'"
$body = @{ model = $modello; stream = $false; prompt = "Scrivi in italiano un breve elenco di 5 articoli per batterie auto." } | ConvertTo-Json
$r = Invoke-RestMethod -Uri http://localhost:11434/api/generate -Method Post -Body $body -ContentType "application/json" -TimeoutSec 600
$tps = [math]::Round($r.eval_count / ($r.eval_duration / 1e9), 1)
$ps = Invoke-RestMethod http://localhost:11434/api/ps
$gpu = if ($ps.models.Count -gt 0 -and $ps.models[0].size -gt 0) { [math]::Round(100 * $ps.models[0].size_vram / $ps.models[0].size) } else { 0 }
Write-Host ""
Write-Host "RISULTATO: $tps parole/secondo, $gpu% sulla scheda grafica" -ForegroundColor Green
if ($gpu -lt 90) { Write-Host "Attenzione: il modello non gira sulla GPU. Aggiorna il driver NVIDIA." -ForegroundColor Yellow }
elseif ($tps -ge 30) { Write-Host "Ottimo: il PC e' adatto all'IA locale." -ForegroundColor Green }

$net = Read-Host "Vuoi che anche altri PC della rete usino questa IA? (S/N)"
if ($net -match '^[sS]') {
    [Environment]::SetEnvironmentVariable("OLLAMA_HOST", "0.0.0.0:11434", "User")
    try { New-NetFirewallRule -DisplayName "Ollama ArcaNote" -Direction Inbound -Protocol TCP -LocalPort 11434 -Action Allow -Profile Private | Out-Null }
    catch { Write-Host "Regola firewall non creata (serve eseguire come amministratore)." -ForegroundColor Yellow }
    Write-Host "Chiudi e riavvia Ollama (icona nella barra) per applicare. Indirizzo per gli altri PC: http://$env:COMPUTERNAME`:11434"
}
Write-Host ""
Read-Host "Fatto. Premi Invio per chiudere"
