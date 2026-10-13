# 安装 Windows 计划任务：每天早上 8:00 采集一次
#
# 以当前用户身份运行（不需要管理员），触发时如果用户没登录，
# 任务仍会运行（-RunLevel Limited 配合 Interactive 登录类型）。
#
# 用法：
#   powershell -ExecutionPolicy Bypass -File install_task.ps1
#   powershell -ExecutionPolicy Bypass -File install_task.ps1 -Time 07:30
#   powershell -ExecutionPolicy Bypass -File install_task.ps1 -Remove
#
# 卸载：-Remove
# 手动跑一次验证：schtasks /Run /TN "ai-frontier-daily"

param(
    [string]$Time = "08:00",
    [string]$TaskName = "ai-frontier-daily",
    [switch]$Remove
)

$ErrorActionPreference = "Stop"
$ProjectDir = $PSScriptRoot

if ($Remove) {
    # schtasks 在任务不存在时会写 stderr 并返回非 0。这是正常状态（还没建过），
    # 不该被 $ErrorActionPreference="Stop" 升级成终止性错误。
    $prev = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    schtasks /Delete /TN $TaskName /F 2>&1 | Out-Null
    $code = $LASTEXITCODE
    $ErrorActionPreference = $prev
    if ($code -eq 0) {
        Write-Host "已删除计划任务 $TaskName"
    } else {
        Write-Host "计划任务 $TaskName 不存在（或删除失败），无需处理。"
    }
    exit 0
}

# 找 Python：必须优先用 DSH 自带运行时，它才带 openpyxl / python-docx。
# 系统 PATH 里的 Python 没有这两个库，选了它 Excel 会静默失败。
# 顺序：AF_PYTHON 覆盖 > DSH 应用内运行时 > ~/.dsh 运行时 > PATH
$py = $null
$candidates = @(
    $env:AF_PYTHON,
    (Join-Path $env:LOCALAPPDATA "Programs\DeepSeek Harness\resources\runtime\primary-runtime\dependencies\python\python.exe"),
    (Join-Path $env:USERPROFILE ".dsh\dsh-runtimes\dsh-primary-runtime\dependencies\python\python.exe")
)
foreach ($c in $candidates) {
    if ($c -and (Test-Path $c)) { $py = $c; break }
}
if (-not $py) {
    $cmd = Get-Command python -ErrorAction SilentlyContinue
    if ($cmd) { $py = $cmd.Source }
}
if (-not $py) {
    Write-Error "找不到 Python。请先确认 DSH 已安装，或用 AF_PYTHON 指定解释器。"
}
Write-Host "使用 Python: $py"

$script = Join-Path $ProjectDir "src\collect.py"
if (-not (Test-Path $script)) {
    Write-Error "找不到 $script —— 请在 ai-frontier 目录下运行本脚本。"
}

# 任务指向包装脚本，而不是直接指向 python。
# 原因：schtasks /TR 无法正确处理"带空格的解释器路径 + 内嵌引号"，
# 实测报 Invalid argument。把引号问题集中到一个 .cmd 里最稳，
# 任务定义也变得可读、可手动跑。
$wrapper = Join-Path $ProjectDir "daily_collect.cmd"
if (-not (Test-Path $wrapper)) {
    Write-Error "找不到 $wrapper —— 它应该和本脚本在同一个目录。"
}
$cmdLine = "`"$wrapper`""

Write-Host "注册计划任务 $TaskName，每天 $Time 运行 ..."
schtasks /Create /TN $TaskName /TR $cmdLine /SC DAILY /ST $Time /F | Out-Null
if ($LASTEXITCODE -ne 0) {
    Write-Error "创建计划任务失败（exit=$LASTEXITCODE）。"
}

# ---------------------------------------------------------------
# 必须改掉 schtasks 的默认设置，否则任务会永远停在 Queued：
#
#   DisallowStartIfOnBatteries 默认 True。笔记本用电池时任务不启动，
#   也完全不报错，状态一直是 Queued。实测踩过这个坑。
#   StopIfGoingOnBatteries 默认 True：跑到一半拔电源会被杀掉。
#   StartWhenAvailable 默认 False：错过触发时刻（比如 8 点机器没开）
#   就永久跳过而不是补跑。
#
# 这是最典型的"静默失败"：任务看起来配好了，实际一次都没跑。
# ---------------------------------------------------------------
try {
    $settings = New-ScheduledTaskSettingsSet `
        -AllowStartIfOnBatteries `
        -DontStopIfGoingOnBatteries `
        -StartWhenAvailable `
        -MultipleInstances IgnoreNew `
        -ExecutionTimeLimit (New-TimeSpan -Minutes 30)
    Set-ScheduledTask -TaskName $TaskName -Settings $settings | Out-Null
    Write-Host "已调整任务设置（允许电池供电、错过后补跑、限时 30 分钟）"
} catch {
    Write-Warning "调整任务设置失败：$_`n任务仍已创建，但笔记本用电池时可能不会运行。"
}

Write-Host ""
Write-Host "完成。任务清单："
schtasks /Query /TN $TaskName /FO LIST | Select-String "TaskName|Next Run|Status|下次运行|状态|任务名"

Write-Host ""
Write-Host "常用操作："
Write-Host "  手动跑一次   schtasks /Run /TN $TaskName"
Write-Host "  查看结果     $ProjectDir\out\logs\"
Write-Host "  删除任务     powershell -File install_task.ps1 -Remove"
Write-Host ""
Write-Host "注意：这个任务只负责【采集】。要生成带判断的正式报告，"
Write-Host "      在 DSH 会话里说『跑一下今天的 AI 前沿日报』，由 ai-frontier-daily 技能完成。"
