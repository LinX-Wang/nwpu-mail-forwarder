$ErrorActionPreference = 'Stop'
Set-Location (Split-Path $PSScriptRoot -Parent)
python -m venv .venv
if ($LASTEXITCODE -ne 0) { throw '创建虚拟环境失败' }
& .\.venv\Scripts\python.exe -m pip install -e '.[test]' tzdata
if ($LASTEXITCODE -ne 0) { throw '依赖安装失败' }
& .\.venv\Scripts\python.exe -m pytest -q
if ($LASTEXITCODE -ne 0) { throw '测试未通过，请先修复' }
