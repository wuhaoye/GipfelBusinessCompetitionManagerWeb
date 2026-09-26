# Gipfel 整改验收 · 一键脚本（Windows 可跑）
#
# 用法（任意目录）：
#   .\tests\ops_check\run_all.ps1              # 全量（含 584 项全量测试，约 10 分钟）
#   .\tests\ops_check\run_all.ps1 -Fast        # 跳过全量测试，只跑各项专项验证
#   .\tests\ops_check\run_all.ps1 -Only C,E    # 只跑指定步骤
#   .\tests\ops_check\run_all.ps1 -KeepDb      # 保留临时数据库副本（默认跑完清理）
#
# 退出码：0 = 全部 PASS；1 = 有 FAIL；2 = 环境问题（venv/副本生成失败）。
# 原始输出落在 tests\ops_check\_artifacts\<步骤>.log，汇总表打印在 stdout。
param(
  [switch]$Fast,
  [switch]$KeepDb,
  [string]$Only = ""
)

$ErrorActionPreference = 'Stop'
$here = $PSScriptRoot
$repo = (Resolve-Path (Join-Path $here '..\..')).Path
$python = Join-Path $repo 'backend\.venv\Scripts\python.exe'

if (-not (Test-Path $python)) {
  Write-Host "[FATAL] 找不到 venv python：$python" -ForegroundColor Red
  exit 2
}

$args = @((Join-Path $here 'verify_all.py'))
if ($Fast)   { $args += '--fast' }
if ($KeepDb) { $args += '--keep-db' }
if ($Only)   { $args += @('--only', $Only) }

& $python @args
exit $LASTEXITCODE
