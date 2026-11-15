# Instala ffmpeg e COLMAP (versao CUDA) dentro de .\tools\
# Uso (PowerShell, na pasta do projeto):
#   powershell -ExecutionPolicy Bypass -File setup\install-tools.ps1
# Depois compila o OpenSplat com setup\build-opensplat.ps1

param(
    [string]$ColmapTag = "latest",   # ex.: "3.11.1" para fixar uma versao
    [switch]$Force
)

$ErrorActionPreference = "Stop"
$ProgressPreference = "SilentlyContinue"   # Invoke-WebRequest fica muito mais rapido
[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12

$Root  = Split-Path -Parent $PSScriptRoot
$Tools = Join-Path $Root "tools"
$Dl    = Join-Path $Tools "_downloads"
New-Item -ItemType Directory -Force -Path $Tools, $Dl | Out-Null

function Step($msg) { Write-Host "`n==> $msg" -ForegroundColor Cyan }

function Get-File($url, $dest) {
    if ((Test-Path $dest) -and -not $Force) { Write-Host "   ja descarregado: $dest"; return }
    Write-Host "   $url"
    Invoke-WebRequest -Uri $url -OutFile $dest -UseBasicParsing
}

function Expand-To($zip, $dest) {
    if (Test-Path $dest) { Remove-Item -Recurse -Force $dest }
    Expand-Archive -Path $zip -DestinationPath $dest -Force
}

# ---------------------------------------------------------------- ffmpeg
Step "ffmpeg"
$ffDir = Join-Path $Tools "ffmpeg"
if ((Get-ChildItem $ffDir -Recurse -Filter ffmpeg.exe -ErrorAction SilentlyContinue) -and -not $Force) {
    Write-Host "   ja instalado"
} else {
    $zip = Join-Path $Dl "ffmpeg.zip"
    Get-File "https://www.gyan.dev/ffmpeg/builds/ffmpeg-release-essentials.zip" $zip
    Expand-To $zip $ffDir
    Write-Host "   OK"
}

# ---------------------------------------------------------------- COLMAP
Step "COLMAP (CUDA)"
$cmDir = Join-Path $Tools "colmap"
if ((Get-ChildItem $cmDir -Recurse -Include COLMAP.bat, colmap.exe -ErrorAction SilentlyContinue) -and -not $Force) {
    Write-Host "   ja instalado"
} else {
    $api = if ($ColmapTag -eq "latest") {
        "https://api.github.com/repos/colmap/colmap/releases/latest"
    } else {
        "https://api.github.com/repos/colmap/colmap/releases/tags/$ColmapTag"
    }
    $rel = Invoke-RestMethod -Uri $api -Headers @{ "User-Agent" = "splat-setup" }
    Write-Host "   release: $($rel.tag_name)"
    $asset = $rel.assets | Where-Object {
        $_.name -match "windows" -and $_.name -match "cuda" -and
        $_.name -notmatch "no-?cuda" -and $_.name -like "*.zip"
    } | Select-Object -First 1
    if (-not $asset) {
        Write-Host "   Assets disponiveis:" -ForegroundColor Yellow
        $rel.assets | ForEach-Object { Write-Host "     $($_.name)" }
        throw "Nao encontrei um zip Windows+CUDA nesta release. Tenta -ColmapTag <versao>."
    }
    $zip = Join-Path $Dl $asset.name
    Get-File $asset.browser_download_url $zip
    Expand-To $zip $cmDir
    Write-Host "   OK: $($asset.name)"
}

# ---------------------------------------------------------------- Python
Step "Python"
$py = Get-Command python -ErrorAction SilentlyContinue
if ($py) { & python --version } else {
    Write-Host "   Python nao encontrado. Instala com:  winget install Python.Python.3.12" -ForegroundColor Yellow
}

Step "Feito. Proximo passo: setup\build-opensplat.ps1"
