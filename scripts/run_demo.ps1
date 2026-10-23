# 一键运行 mock 演示（不会移动鼠标、不需要网络与大模型 Key）
$ErrorActionPreference = "Stop"
$projectRoot = Split-Path -Parent $PSScriptRoot
Push-Location $projectRoot
try {
    Write-Host "项目目录：$projectRoot"
    python run.py demo
}
finally {
    Pop-Location
}
