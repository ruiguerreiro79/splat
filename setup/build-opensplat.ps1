# Compila o OpenSplat (https://github.com/pierotofy/OpenSplat) com CUDA em Windows
# e copia opensplat.exe + DLLs para .\tools\opensplat\
#
# Pre-requisitos (instalar uma vez, manualmente):
#   1. Visual Studio 2022 com "Desktop development with C++"   (winget install Microsoft.VisualStudio.2022.Community)
#   2. CMake >= 3.24                                            (winget install Kitware.CMake)
#   3. Git                                                      (winget install Git.Git)
#   4. CUDA Toolkit com a MESMA versao do libtorch (default 11.8)
#        https://developer.nvidia.com/cuda-11-8-0-download-archive
#
# Uso:
#   powershell -ExecutionPolicy Bypass -File setup\build-opensplat.ps1
#   # RTX 50xx (Blackwell) precisa de CUDA 12.8 e libtorch recente, ex.:
#   powershell -ExecutionPolicy Bypass -File setup\build-opensplat.ps1 -CudaTag cu128 -TorchVersion 2.7.1
#
# A combinacao default (CUDA 11.8, libtorch 2.1.2, OpenCV 4.9.0) e a que o README
# do OpenSplat indica como testada em Windows.

param(
    [string]$TorchVersion  = "2.1.2",
    [string]$CudaTag       = "cu118",     # cu118, cu121, cu124, cu126, cu128 ...
    [string]$OpenCVVersion = "4.9.0",
    [string]$CudaArch      = "",          # ex.: 86 (RTX 30), 89 (RTX 40), 120 (RTX 50). Vazio = default
    [string]$GitRef        = "main",
    [switch]$Clean
)

$ErrorActionPreference = "Stop"
$ProgressPreference = "SilentlyContinue"
[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12

$Root  = Split-Path -Parent $PSScriptRoot
$Tools = Join-Path $Root "tools"
$Dl    = Join-Path $Tools "_downloads"
$Deps  = Join-Path $Tools "deps"
$Src   = Join-Path $Tools "src\OpenSplat"
$Build = Join-Path $Src "build"
$Out   = Join-Path $Tools "opensplat"
New-Item -ItemType Directory -Force -Path $Tools, $Dl, $Deps | Out-Null

function Step($msg) { Write-Host "`n==> $msg" -ForegroundColor Cyan }
function Need($ok, $msg) { if (-not $ok) { Write-Host "   FALTA: $msg" -ForegroundColor Red; $script:missing = $true } }

# ---------------------------------------------------------------- pre-requisitos
Step "A verificar pre-requisitos"
$missing = $false

$vswhere = Join-Path ${env:ProgramFiles(x86)} "Microsoft Visual Studio\Installer\vswhere.exe"
$vcvars = $null
if (Test-Path $vswhere) {
    $vsPath = & $vswhere -latest -products * -requires Microsoft.VisualStudio.Component.VC.Tools.x86.x64 -property installationPath
    if ($vsPath) { $vcvars = Join-Path $vsPath "VC\Auxiliary\Build\vcvars64.bat" }
}
Need ($vcvars -and (Test-Path $vcvars)) "Visual Studio com ferramentas C++ (vcvars64.bat)"
if ($vcvars) { Write-Host "   VS:    $vcvars" }

Need (Get-Command git -ErrorAction SilentlyContinue) "git"
Need (Get-Command cmake -ErrorAction SilentlyContinue) "cmake"

$nvcc = if ($env:CUDA_PATH) { Join-Path $env:CUDA_PATH "bin\nvcc.exe" } else { $null }
Need ($nvcc -and (Test-Path $nvcc)) "CUDA Toolkit (variavel CUDA_PATH / nvcc.exe)"
if ($nvcc -and (Test-Path $nvcc)) {
    $nv = (& $nvcc --version | Select-String "release ([0-9]+)\.([0-9]+)").Matches
    if ($nv) {
        $have = "cu" + $nv[0].Groups[1].Value + $nv[0].Groups[2].Value
        Write-Host "   CUDA:  $($nv[0].Value)  ($have)"
        if ($have -ne $CudaTag) {
            Write-Host "   AVISO: CUDA instalado ($have) difere do libtorch pedido ($CudaTag)." -ForegroundColor Yellow
            Write-Host "          Instala o CUDA certo ou corre com -CudaTag $have e um -TorchVersion compativel." -ForegroundColor Yellow
        }
    }
}
if ($missing) { throw "Instala os pre-requisitos em falta (ver topo deste script) e volta a correr." }

# ---------------------------------------------------------------- libtorch
Step "libtorch $TorchVersion+$CudaTag"
$torchDir = Join-Path $Deps "libtorch-$TorchVersion-$CudaTag"
if (-not (Test-Path (Join-Path $torchDir "libtorch\share\cmake\Torch"))) {
    $zip = Join-Path $Dl "libtorch-$TorchVersion-$CudaTag.zip"
    $url = "https://download.pytorch.org/libtorch/$CudaTag/libtorch-win-shared-with-deps-$TorchVersion%2B$CudaTag.zip"
    if (-not (Test-Path $zip)) {
        Write-Host "   $url  (~2-3 GB, demora)"
        Invoke-WebRequest -Uri $url -OutFile $zip -UseBasicParsing
    }
    if (Test-Path $torchDir) { Remove-Item -Recurse -Force $torchDir }
    Expand-Archive -Path $zip -DestinationPath $torchDir -Force
}
$torchCmake = (Join-Path $torchDir "libtorch") -replace "\\", "/"
Write-Host "   OK: $torchCmake"

# ---------------------------------------------------------------- OpenCV
Step "OpenCV $OpenCVVersion"
$cvDir = Join-Path $Deps "opencv-$OpenCVVersion"
$cvBuild = Join-Path $cvDir "opencv\build"
if (-not (Test-Path $cvBuild)) {
    $exe = Join-Path $Dl "opencv-$OpenCVVersion-windows.exe"
    if (-not (Test-Path $exe)) {
        $url = "https://github.com/opencv/opencv/releases/download/$OpenCVVersion/opencv-$OpenCVVersion-windows.exe"
        Write-Host "   $url"
        Invoke-WebRequest -Uri $url -OutFile $exe -UseBasicParsing
    }
    New-Item -ItemType Directory -Force -Path $cvDir | Out-Null
    # O instalador e um 7-Zip auto-extraivel.
    Start-Process -FilePath $exe -ArgumentList "-o`"$cvDir`"", "-y" -Wait -NoNewWindow
}
$cvCmake = $cvBuild -replace "\\", "/"
Write-Host "   OK: $cvCmake"

# ---------------------------------------------------------------- codigo
Step "Codigo do OpenSplat ($GitRef)"
if (-not (Test-Path (Join-Path $Src ".git"))) {
    git clone https://github.com/pierotofy/OpenSplat $Src
}
git -C $Src fetch --tags origin
git -C $Src checkout $GitRef
if ($GitRef -eq "main") { git -C $Src pull --ff-only origin main }
if ($Clean -and (Test-Path $Build)) { Remove-Item -Recurse -Force $Build }

# ---------------------------------------------------------------- compilar
Step "A compilar (pode demorar 10-30 min)"
$cmakeArgs = "-S `"$Src`" -B `"$Build`" -DCMAKE_BUILD_TYPE=Release " +
             "-DCMAKE_PREFIX_PATH=`"$torchCmake`" -DOPENCV_DIR=`"$cvCmake`" -DGPU_RUNTIME=CUDA"
if ($CudaArch) { $cmakeArgs += " -DCMAKE_CUDA_ARCHITECTURES=$CudaArch" }
$cmdline = "call `"$vcvars`" && cmake $cmakeArgs && cmake --build `"$Build`" --config Release --parallel"
cmd /c $cmdline
if ($LASTEXITCODE -ne 0) { throw "A compilacao falhou (codigo $LASTEXITCODE). Ve o erro acima." }

# ---------------------------------------------------------------- instalar
Step "A copiar para $Out"
$exeBuilt = Get-ChildItem $Build -Recurse -Filter opensplat.exe | Select-Object -First 1
if (-not $exeBuilt) { throw "Nao encontrei opensplat.exe em $Build" }
if (Test-Path $Out) { Remove-Item -Recurse -Force $Out }
New-Item -ItemType Directory -Force -Path $Out | Out-Null
Copy-Item $exeBuilt.FullName $Out
Copy-Item (Join-Path $exeBuilt.DirectoryName "*.dll") $Out -ErrorAction SilentlyContinue
Copy-Item (Join-Path $torchDir "libtorch\lib\*.dll") $Out
Get-ChildItem (Join-Path $cvBuild "x64") -Recurse -Filter "opencv_world*.dll" | Copy-Item -Destination $Out

& (Join-Path $Out "opensplat.exe") --help | Select-Object -First 5
Step "OpenSplat pronto: $Out\opensplat.exe"
Write-Host "   Testa tudo com:  python splat.py check"
