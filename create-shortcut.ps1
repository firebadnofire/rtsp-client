# Get directory of this script (the repo root)
$project = Split-Path -Parent $MyInvocation.MyCommand.Path

# Path to run.ps1 inside the repo
$runner = Join-Path $project "run.ps1"

# Path to user's desktop
$desktop = [Environment]::GetFolderPath("Desktop")

# Shortcut path
$shortcutPath = Join-Path $desktop "rtsp-viewer.lnk"

# Icon path
$icon = Join-Path $project "icons\firsticon.ico"

# Create WScript shell COM object
$ws = New-Object -ComObject WScript.Shell

# Create shortcut
$sc = $ws.CreateShortcut($shortcutPath)

# Configure target
$sc.TargetPath = "powershell.exe"
$sc.Arguments = "-WindowStyle Hidden -ExecutionPolicy Bypass -File `"$runner`""
$sc.WorkingDirectory = $project

# Icon
$sc.IconLocation = $icon

# Save
$sc.Save()

Write-Host "Shortcut created at $shortcutPath"
