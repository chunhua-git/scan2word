# Build both distributables (folder version + single-file version) and verify them.
#
#   powershell -ExecutionPolicy Bypass -File packaging\build.ps1
#
# Output:
#   dist\scan2word\            folder version (fast startup)
#   dist\scan2word绿色版.zip    zipped folder version  <-- send this one
#   dist\scan2word单文件版.exe  single-file version     <-- or send this one
#
# IMPORTANT: keep this file ASCII-ONLY, including comments and string literals.
# Windows PowerShell 5.1 reads .ps1 as ANSI unless it has a UTF-8 BOM, so any
# non-ASCII character turns into mojibake and can swallow the following line.
# Chinese filenames are produced/handled by Python (packaging\post_build.py).

$ErrorActionPreference = 'Stop'
$proj = Split-Path -Parent $PSScriptRoot
$env:PYTHONUTF8 = '1'
$env:PYTHONIOENCODING = 'utf-8'
$env:PYTHONPATH = "$proj\vendor"

# Resolve a Python interpreter: prefer one on PATH, then common install locations.
$python = $null
foreach ($cmd in @('python', 'python3', 'py')) {
    $c = Get-Command $cmd -ErrorAction SilentlyContinue
    if ($c) { $python = $c.Source; break }
}
if (-not $python) {
    foreach ($p in @("$env:LOCALAPPDATA\Programs\Python\Python312\python.exe",
                     'C:\Python312\python.exe', 'C:\Python311\python.exe')) {
        if (Test-Path $p) { $python = $p; break }
    }
}
if (-not $python) { throw "No Python interpreter found. Install Python 3.10+ first." }
Write-Host "== python: $python"

Write-Host "== project: $proj"

Write-Host "`n== 1/5 clean old output"
Remove-Item "$proj\build", "$proj\dist" -Recurse -Force -ErrorAction SilentlyContinue

Write-Host "`n== 2/5 build folder version"
& $python -m PyInstaller "$proj\packaging\scan2word.spec" --noconfirm --clean --distpath "$proj\dist" --workpath "$proj\build"
if ($LASTEXITCODE -ne 0) { throw "PyInstaller (folder) failed (exit $LASTEXITCODE)" }

Write-Host "`n== 3/5 attach docs/license, size report, and zip the folder"
& $python "$proj\packaging\post_build.py"
if ($LASTEXITCODE -ne 0) { throw "post_build failed (exit $LASTEXITCODE)" }

Write-Host "`n== 4/5 self-test the folder version"
$out = "$proj\dist\scan2word"
& $python "$proj\packaging\verify_exe.py" "$out\scan2word.exe"
if ($LASTEXITCODE -ne 0) { throw "folder version self-test FAILED" }

Write-Host "`n== 5/5 build + self-test the single-file version"
& $python -m PyInstaller "$proj\packaging\scan2word_onefile.spec" --noconfirm --clean --distpath "$proj\dist" --workpath "$proj\build_onefile"
if ($LASTEXITCODE -ne 0) { throw "PyInstaller (onefile) failed (exit $LASTEXITCODE)" }

$onefile = Get-ChildItem "$proj\dist" -Filter "*.exe" | Select-Object -First 1
if ($onefile) {
    & $python "$proj\packaging\verify_exe.py" $onefile.FullName
    if ($LASTEXITCODE -ne 0) { throw "single-file version self-test FAILED" }
} else {
    throw "single-file exe not found in dist"
}

Write-Host "`n== DONE" -ForegroundColor Green
Get-ChildItem "$proj\dist" | ForEach-Object {
    if ($_.PSIsContainer) {
        $s = (Get-ChildItem $_.FullName -Recurse -File | Measure-Object Length -Sum).Sum / 1MB
        Write-Host ("  [dir ] {0,-28} {1,6:N0} MB" -f $_.Name, $s)
    } else {
        Write-Host ("  [file] {0,-28} {1,6:N0} MB" -f $_.Name, ($_.Length / 1MB))
    }
}
