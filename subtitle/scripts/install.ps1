#Requires -Version 5.1
<#
.SYNOPSIS
    115Translate 一键装机脚本（Windows）。

.DESCRIPTION
    依次完成：
      1. 创建 Python 虚拟环境 .venv
      2. 用国内镜像源安装 requirements.txt（含 CUDA 运行库）
      3. 下载 ffmpeg / ffprobe 到 bin\ffmpeg
      4. 下载 llama.cpp 官方 CUDA 预编译包到 bin\llama.cpp
      5. 下载翻译用 GGUF 模型与 faster-whisper-large-v3 到 models\
      6. 生成 config.yaml（若不存在）
      7. 运行环境自检

.EXAMPLE
    powershell -ExecutionPolicy Bypass -File .\scripts\install.ps1
    powershell -ExecutionPolicy Bypass -File .\scripts\install.ps1 -Quant Q5_K_M -SkipFfmpeg
#>
[CmdletBinding()]
param(
    [string]$Python = "",
    [string]$PipMirror = "https://pypi.tuna.tsinghua.edu.cn/simple",
    [string]$Quant = "Q4_K_M",
    [string]$CudaTag = "12.4",
    [switch]$SkipDeps,
    [switch]$SkipFfmpeg,
    [switch]$SkipLlamaCpp,
    [switch]$SkipModels,
    [switch]$SkipDoctor
)

$ErrorActionPreference = "Stop"
[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12

$Root = Split-Path -Parent $PSScriptRoot
$VenvPython = Join-Path $Root ".venv\Scripts\python.exe"
# GitHub 镜像顺序：实测国内网络下 gh-proxy 对 release 资源最快（数 MB/s），
# 直连往往只有几十 KB/s，所以把直连放最后当兜底。
$GhMirrors = @("https://gh-proxy.com/", "https://ghfast.top/", "https://gh-proxy.net/", "")

# --------------------------------------------------------------------------- #
#  小工具
# --------------------------------------------------------------------------- #

function Write-Step {
    param([int]$No, [string]$Text)
    Write-Host ""
    Write-Host ("=" * 66) -ForegroundColor DarkGray
    Write-Host " [$No] $Text" -ForegroundColor Cyan
    Write-Host ("=" * 66) -ForegroundColor DarkGray
}

function Write-Ok   { param([string]$M) Write-Host "  [OK]   $M" -ForegroundColor Green }
function Write-Info { param([string]$M) Write-Host "  [..]   $M" -ForegroundColor Yellow }
function Write-Warn { param([string]$M) Write-Host "  [!!]   $M" -ForegroundColor Magenta }
function Write-Fail { param([string]$M) Write-Host "  [XX]   $M" -ForegroundColor Red }

function Resolve-Python {
    if ($Python -and (Test-Path $Python)) { return (Resolve-Path $Python).Path }
    $candidates = @(
        "D:\DevHub\python\python.exe",
        "C:\Python311\python.exe",
        "C:\Python312\python.exe"
    )
    foreach ($c in $candidates) { if (Test-Path $c) { return $c } }
    $cmd = Get-Command python -ErrorAction SilentlyContinue
    if ($cmd) {
        # 微软商店的存根会静默失败，这里验证一下真的能跑
        try {
            $v = & $cmd.Source -c "import sys;print(sys.version_info[:2])" 2>$null
            if ($v) { return $cmd.Source }
        } catch {}
    }
    $py = Get-Command py -ErrorAction SilentlyContinue
    if ($py) { return $py.Source }
    return $null
}

function Get-WithMirrors {
    <#
    依次用直连 / GitHub 镜像下载，返回保存到的路径；全部失败返回 $null。
    用 curl.exe 而不是 Invoke-WebRequest：可以分别设置「连接超时」和「总耗时上限」，
    避免直连 GitHub 时干等十分钟才切到镜像。
    #>
    param([string]$Url, [string]$Dest, [int]$ConnectTimeout = 15, [int]$MaxTime = 1800)

    $curl = Join-Path $env:SystemRoot "System32\curl.exe"
    $hasCurl = Test-Path $curl

    foreach ($m in $GhMirrors) {
        $target = if ($m) { "$m$Url" } else { $Url }
        $host_ = ([Uri]$target).Host
        if (Test-Path $Dest) { Remove-Item $Dest -Force -ErrorAction SilentlyContinue }
        Write-Info "尝试 $host_ ..."
        try {
            if ($hasCurl) {
                & $curl -L --fail --silent --show-error `
                    --connect-timeout $ConnectTimeout --max-time $MaxTime `
                    -o $Dest $target 2>&1 | Out-Null
            } else {
                $ProgressPreference = "SilentlyContinue"
                Invoke-WebRequest -Uri $target -OutFile $Dest -UseBasicParsing -TimeoutSec 180
            }
            if ((Test-Path $Dest) -and ((Get-Item $Dest).Length -gt 1024)) {
                Write-Ok "下载成功（来自 $host_，$([math]::Round((Get-Item $Dest).Length/1MB,1)) MB）"
                return $Dest
            }
        } catch {
            Write-Warn "$host_ 失败：$($_.Exception.Message)"
        }
        Write-Warn "$host_ 无响应或未拿到完整文件，换下一个源"
    }
    return $null
}

function Move-Flat {
    <# 官方 zip 里常套一层版本目录，摊平到目标目录，方便配置里写死路径 #>
    param([string]$Dir, [string]$AnchorFile)
    if (Test-Path (Join-Path $Dir $AnchorFile)) { return }
    $found = Get-ChildItem -Path $Dir -Recurse -Filter $AnchorFile -File -ErrorAction SilentlyContinue |
             Select-Object -First 1
    if (-not $found) { return }
    $src = $found.Directory.FullName
    if ($src -eq (Resolve-Path $Dir).Path) { return }
    Write-Info "摊平子目录：$($found.Directory.Name)"
    Get-ChildItem -Path $src -Force | ForEach-Object {
        Move-Item -Path $_.FullName -Destination $Dir -Force -ErrorAction SilentlyContinue
    }
    Remove-Item $src -Recurse -Force -ErrorAction SilentlyContinue
}

function Get-GithubReleases {
    <#
    拉取 release 列表。
    注意：llama.cpp 的仓库已迁到 ggml-org/llama.cpp，且二进制按 commit 发版
    （tag 形如 b10797），不带 "latest" 标记的 v0.x.x release 里只有源码说明，
    所以这里取列表、由调用方挑出真正带 win-cuda 资源的那个。
    #>
    param([string]$Repo, [int]$Count = 15)
    $api = "https://api.github.com/repos/$Repo/releases?per_page=$Count"
    foreach ($m in $GhMirrors) {
        $target = if ($m) { "$m$api" } else { $api }
        try {
            $ProgressPreference = "SilentlyContinue"
            $json = Invoke-RestMethod -Uri $target -Headers @{ "User-Agent" = "115Translate" } -TimeoutSec 90
            if ($json) { return @($json) }
        } catch {
            Write-Warn "读取发布信息失败（$([Uri]$target).Host）：$($_.Exception.Message)"
        }
    }
    return $null
}

function Select-LlamaAsset {
    <# 从一个 release 里挑出合适的 Windows CUDA 包 #>
    param($Release, [string]$CudaTag)
    $main = @($Release.assets | Where-Object {
        $_.name -match "^llama-.*-bin-win-cuda-.*-x64\.zip$" -and $_.name -notmatch "cudart"
    })
    $pick = $main | Where-Object { $_.name -match [regex]::Escape("cuda-$CudaTag") } | Select-Object -First 1
    if (-not $pick) { $pick = $main | Select-Object -First 1 }
    return $pick
}


# --------------------------------------------------------------------------- #
Write-Host ""
Write-Host "  115Translate 一键装机" -ForegroundColor Cyan
Write-Host "  项目目录：$Root" -ForegroundColor DarkGray
Write-Host ""

# --------------------------------------------------------------------------- #
#  1. Python 虚拟环境
# --------------------------------------------------------------------------- #
Write-Step 1 "准备 Python 虚拟环境"

$sysPy = Resolve-Python
if (-not $sysPy) {
    Write-Fail "找不到可用的 Python 3.9+，请先安装（推荐 3.11）。"
    exit 1
}
Write-Info "使用系统 Python：$sysPy"
& $sysPy -c "import sys;sys.exit(0 if sys.version_info>=(3,9) else 1)"
if ($LASTEXITCODE -ne 0) {
    Write-Fail "Python 版本过低，需要 3.9+（推荐 3.11）。"
    exit 1
}

if (-not (Test-Path $VenvPython)) {
    Write-Info "创建 .venv ..."
    & $sysPy -m venv (Join-Path $Root ".venv")
    if ($LASTEXITCODE -ne 0) { Write-Fail "venv 创建失败"; exit 1 }
    Write-Ok "已创建 .venv"
} else {
    Write-Ok ".venv 已存在"
}
$Py = $VenvPython

# --------------------------------------------------------------------------- #
#  2. 依赖
# --------------------------------------------------------------------------- #
if (-not $SkipDeps) {
    Write-Step 2 "安装 Python 依赖（镜像源：$PipMirror）"
    Write-Info "升级 pip ..."
    & $Py -m pip install --upgrade pip -i $PipMirror --disable-pip-version-check -q
    Write-Info "安装 requirements.txt ..."
    & $Py -m pip install -r (Join-Path $Root "requirements.txt") -i $PipMirror --disable-pip-version-check
    if ($LASTEXITCODE -ne 0) {
        Write-Fail "依赖安装失败。可换镜像重试，例如 -PipMirror https://mirrors.aliyun.com/pypi/simple"
        exit 1
    }
    Write-Ok "依赖安装完成"
} else {
    Write-Step 2 "安装 Python 依赖（已跳过）"
}

# --------------------------------------------------------------------------- #
#  3. ffmpeg
# --------------------------------------------------------------------------- #
$ffDir = Join-Path $Root "bin\ffmpeg"
if (-not $SkipFfmpeg) {
    Write-Step 3 "下载 ffmpeg"
    $ffExe = Join-Path $ffDir "ffmpeg.exe"
    if (Test-Path $ffExe) {
        Write-Ok "已存在：$ffExe"
    } else {
        $zip = Join-Path $env:TEMP "ffmpeg-win64.zip"
        $url = "https://github.com/BtbN/FFmpeg-Builds/releases/download/latest/ffmpeg-master-latest-win64-gpl.zip"
        $got = Get-WithMirrors -Url $url -Dest $zip
        if (-not $got) {
            Write-Warn "ffmpeg 下载失败。可手动下载后把 ffmpeg.exe / ffprobe.exe 放到 $ffDir"
        } else {
            $tmp = Join-Path $env:TEMP "ffmpeg-extract"
            if (Test-Path $tmp) { Remove-Item $tmp -Recurse -Force }
            Expand-Archive -Path $zip -DestinationPath $tmp -Force
            $found = Get-ChildItem -Path $tmp -Recurse -Filter "ffmpeg.exe" | Select-Object -First 1
            if ($found) {
                New-Item -ItemType Directory -Force -Path $ffDir | Out-Null
                Copy-Item (Join-Path $found.Directory.FullName "ffmpeg.exe") $ffDir -Force
                $probe = Join-Path $found.Directory.FullName "ffprobe.exe"
                if (Test-Path $probe) { Copy-Item $probe $ffDir -Force }
                Write-Ok "已安装到 $ffDir"
            } else {
                Write-Warn "解压后没找到 ffmpeg.exe"
            }
            Remove-Item $tmp -Recurse -Force -ErrorAction SilentlyContinue
        }
    }
} else {
    Write-Step 3 "下载 ffmpeg（已跳过）"
}

# --------------------------------------------------------------------------- #
#  4. llama.cpp
# --------------------------------------------------------------------------- #
$llDir = Join-Path $Root "bin\llama.cpp"
if (-not $SkipLlamaCpp) {
    Write-Step 4 "下载 llama.cpp（Windows CUDA 预编译版）"
    $srvExe = Join-Path $llDir "llama-server.exe"
    if (Test-Path $srvExe) {
        Write-Ok "已存在：$srvExe"
    } else {
        $rels = Get-GithubReleases -Repo "ggml-org/llama.cpp" -Count 15
        if (-not $rels) {
            Write-Warn "拿不到 release 信息，跳过。请手动下载后解压到 $llDir"
        } else {
            # 挑出第一个真正带 win-cuda 二进制的 release（多数都是，v0.x.x 除外）
            $rel = $null; $pick = $null
            foreach ($r in $rels) {
                $p = Select-LlamaAsset -Release $r -CudaTag $CudaTag
                if ($p) { $rel = $r; $pick = $p; break }
            }
            if (-not $rel) {
                Write-Warn "最近 15 个 release 里都没有 win-cuda-x64 包，请手动下载。"
            } else {
                Write-Info "选中版本：$($rel.tag_name)"
                Write-Info "选中资源：$($pick.name)（$([math]::Round($pick.size/1MB,1)) MB）"
                $zip = Join-Path $env:TEMP $pick.name
                $got = Get-WithMirrors -Url $pick.browser_download_url -Dest $zip
                if ($got) {
                    New-Item -ItemType Directory -Force -Path $llDir | Out-Null
                    Expand-Archive -Path $zip -DestinationPath $llDir -Force
                    Move-Flat -Dir $llDir -AnchorFile "llama-server.exe"
                    Write-Ok "已解压到 $llDir"

                    # CUDA 版还需要 cudart 运行库，官方单独打包了
                    $rt = @($rel.assets | Where-Object {
                        $_.name -match "^cudart-llama.*win-cuda" -and $_.name -match [regex]::Escape($CudaTag)
                    }) | Select-Object -First 1
                    if (-not $rt) {
                        $rt = @($rel.assets | Where-Object { $_.name -match "^cudart-llama.*win-cuda.*x64" }) | Select-Object -First 1
                    }
                    if ($rt) {
                        Write-Info "补充 CUDA 运行库：$($rt.name)"
                        $rtZip = Join-Path $env:TEMP $rt.name
                        if (Get-WithMirrors -Url $rt.browser_download_url -Dest $rtZip) {
                            Expand-Archive -Path $rtZip -DestinationPath $llDir -Force
                            Move-Flat -Dir $llDir -AnchorFile "llama-server.exe"
                        }
                    }
                } else {
                    Write-Warn "llama.cpp 下载失败，请手动下载解压到 $llDir"
                }
            }
        }
    }
} else {
    Write-Step 4 "下载 llama.cpp（已跳过）"
}

# --------------------------------------------------------------------------- #
#  5. 模型
# --------------------------------------------------------------------------- #
if (-not $SkipModels) {
    Write-Step 5 "下载模型（ModelScope 优先，支持断点续传）"
    & $Py (Join-Path $Root "scripts\fetch_models.py") --all --quant $Quant
    if ($LASTEXITCODE -ne 0) {
        Write-Warn "模型下载未完全成功，重跑本脚本可续传。"
    }
} else {
    Write-Step 5 "下载模型（已跳过）"
}

# --------------------------------------------------------------------------- #
#  6. 配置文件
# --------------------------------------------------------------------------- #
Write-Step 6 "配置文件"
$cfg = Join-Path $Root "config.yaml"
if (Test-Path $cfg) {
    Write-Ok "config.yaml 已存在"
} else {
    Copy-Item (Join-Path $Root "config.example.yaml") $cfg
    Write-Ok "已从模板生成 config.yaml"
    Write-Warn "下一步：编辑 config.yaml，填写 webdav.base_url / username / password / root"
}

# --------------------------------------------------------------------------- #
#  7. 自检
# --------------------------------------------------------------------------- #
if (-not $SkipDoctor) {
    Write-Step 7 "环境自检"
    & $Py (Join-Path $Root "run.py") doctor
}

Write-Host ""
Write-Host "  装机流程结束。" -ForegroundColor Cyan
Write-Host "  下一步：编辑 config.yaml，然后跑  python .venv\Scripts\python.exe run.py scan  " -ForegroundColor Yellow
Write-Host ""
