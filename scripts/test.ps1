$ErrorActionPreference = "Stop"
python (Join-Path $PSScriptRoot "test.py") @args
exit $LASTEXITCODE
