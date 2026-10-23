# Либерея — портативная сборка для Windows (встроенный Python, без установки).
# Запускается в GitHub Actions (windows-latest) или вручную:  powershell -File scripts\build-windows.ps1 -Version 1.0.0
param(
  [string]$Version = "dev",
  [string]$PyVersion = "3.12.10"
)
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
$dist = Join-Path $root "dist"
$out = Join-Path $dist "Liberea"
Remove-Item -Recurse -Force $dist -ErrorAction SilentlyContinue
New-Item -ItemType Directory -Force $out | Out-Null

# 1. Встроенный Python
$pyZip = Join-Path $dist "python-embed.zip"
Invoke-WebRequest "https://www.python.org/ftp/python/$PyVersion/python-$PyVersion-embed-amd64.zip" -OutFile $pyZip
Expand-Archive $pyZip (Join-Path $out "python")
Remove-Item $pyZip
# ._pth: включить site-packages и папку программы (..)
$pth = Get-ChildItem (Join-Path $out "python") -Filter "python*._pth" | Select-Object -First 1
$lines = Get-Content $pth.FullName | ForEach-Object { if ($_ -eq "#import site") { "import site" } else { $_ } }
$lines += ".."
Set-Content $pth.FullName $lines -Encoding ascii

# 2. pip и зависимости
$py = Join-Path $out "python\python.exe"
Invoke-WebRequest "https://bootstrap.pypa.io/get-pip.py" -OutFile (Join-Path $dist "get-pip.py")
& $py (Join-Path $dist "get-pip.py") --no-warn-script-location
if ($LASTEXITCODE -ne 0) { throw "get-pip failed" }
& $py -m pip install --no-warn-script-location -r (Join-Path $root "requirements.txt")
if ($LASTEXITCODE -ne 0) { throw "pip install failed" }
Remove-Item (Join-Path $dist "get-pip.py")

# 3. Программа
foreach ($item in @("app", "fonts", "manage.py", "config.example.toml", "requirements.txt", "LICENSE",
                    "README.md", "README.en.md", "CHANGELOG.md",
                    "run.bat", "scan.bat", "scan-inpx.bat", "get-model.bat", "update.bat")) {
  Copy-Item -Recurse (Join-Path $root $item) $out
}
Get-ChildItem $out -Recurse -Directory -Filter "__pycache__" | Remove-Item -Recurse -Force

# 4. Проверка: программа импортируется встроенным Python
Push-Location $out
& $py -c "import app.main, app.opds; print('import ok')"
if ($LASTEXITCODE -ne 0) { throw "smoke import failed" }
Pop-Location
Get-ChildItem $out -Recurse -Directory -Filter "__pycache__" | Remove-Item -Recurse -Force
Remove-Item -Recurse -Force (Join-Path $out "data") -ErrorAction SilentlyContinue

# 5. Архив
$zip = Join-Path $dist "Liberea-$Version-windows-x64.zip"
Compress-Archive -Path $out -DestinationPath $zip
Write-Host "Готово: $zip"
