param(
    [ValidateSet('demo', 'check', 'check-ui', 'check-codex', 'inspect', 'inspect-ocr', 'draft-ocr', 'suggest', 'send')]
    [string]$Mode = 'demo'
)
$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
Set-Location -LiteralPath $projectRoot
$pythonPath = Join-Path $projectRoot '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $pythonPath)) {
    throw '请先双击项目根目录的恋聊教练.vbs 完成安装。'
}
$env:PYTHONUTF8 = '1'
$env:PYTHONIOENCODING = 'utf-8'
$env:PIP_CACHE_DIR = Join-Path $projectRoot 'work\pip-cache'
$env:TEMP = Join-Path $projectRoot 'work\tmp'
$env:TMP = $env:TEMP
New-Item -ItemType Directory -Force -Path $env:TEMP | Out-Null
if ($Mode -eq 'demo') {
    & $pythonPath -m opendate --config examples/config.wechat.demo.yaml --mock run --cycles 1 --no-interactive
    exit $LASTEXITCODE
}
if (-not (Test-Path -LiteralPath (Join-Path $projectRoot 'config.wechat.yaml'))) {
    throw '请先复制 config.wechat.example.yaml 为 config.wechat.yaml，再填写自己的显示名与指定好友。'
}
switch ($Mode) {
    'check' { & $pythonPath -m opendate --config config.wechat.yaml wechat check }
    'check-ui' { & $pythonPath -m opendate --config config.wechat.yaml wechat check --ui }
    'check-codex' { & $pythonPath -m opendate --config config.wechat.yaml wechat check-codex }
    'inspect' { & $pythonPath -m opendate --config config.wechat.yaml wechat inspect }
    'inspect-ocr' { & $pythonPath -m opendate --config config.wechat.yaml wechat inspect --ocr }
    'draft-ocr' { & $pythonPath -m opendate --config config.wechat.yaml wechat draft-ocr }
    'suggest' { & $pythonPath -m opendate --config config.wechat.yaml run --cycles 1 --no-interactive }
    'send' { & $pythonPath -m opendate --config config.wechat.yaml run --cycles 1 --wechat-send }
}
exit $LASTEXITCODE
