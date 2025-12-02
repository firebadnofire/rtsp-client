# Resolve directory of this script
$project = Split-Path -Parent $MyInvocation.MyCommand.Path

$python = Join-Path $project "venv\Scripts\python.exe"
$script = Join-Path $project "main.py"

& $python $script
