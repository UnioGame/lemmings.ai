[CmdletBinding()]
param(
    [Parameter(ValueFromRemainingArguments = $true)]
    [string[]]$Arguments
)

$ErrorActionPreference = 'Stop'
$handler = Join-Path $PSScriptRoot '../skills/lemmings/scripts/run.py'
$payload = [Console]::In.ReadToEnd()
$cwd = (Get-Location).Path
try {
    $parsed = $payload | ConvertFrom-Json -ErrorAction Stop
    if ($parsed.cwd) { $cwd = [string]$parsed.cwd }
} catch {
    # The existing handler remains responsible for payload validation whenever
    # the runtime marker is active.
}
if (-not (Test-Path -LiteralPath $cwd -PathType Container)) {
    [Console]::Error.WriteLine("Lemmings hook cwd does not exist: $cwd")
    exit 1
}
$git = Get-Command git -ErrorAction SilentlyContinue
if (-not $git) {
    [Console]::Error.WriteLine('Lemmings hook cannot determine runtime state because Git is unavailable.')
    exit 1
}
$inside = ((& $git.Source -C $cwd rev-parse --is-inside-work-tree 2>$null) | Select-Object -First 1)
if ($LASTEXITCODE -ne 0 -or $inside -ne 'true') {
    [Console]::Out.WriteLine('{}')
    exit 0
}
$common = ((& $git.Source -C $cwd rev-parse --path-format=absolute --git-common-dir 2>$null) | Select-Object -First 1)
if ($LASTEXITCODE -ne 0 -or -not $common) {
    [Console]::Error.WriteLine('Lemmings hook could not resolve the Git common directory.')
    exit 1
}
$marker = Join-Path $common.ToString().Trim() 'lemmings/active.json'
if (-not (Test-Path -LiteralPath $marker -PathType Leaf)) {
    [Console]::Out.WriteLine('{}')
    exit 0
}

$choices = @(
    @{ Command = 'py'; Prefix = @('-3') },
    @{ Command = 'python3'; Prefix = @() },
    @{ Command = 'python'; Prefix = @() }
)
$payloadFile = [IO.Path]::GetTempFileName()
try {
    [IO.File]::WriteAllText($payloadFile, $payload, [Text.UTF8Encoding]::new($false))
    foreach ($choice in $choices) {
        $command = Get-Command $choice.Command -ErrorAction SilentlyContinue
        if (-not $command) { continue }
        & $command.Source @($choice.Prefix) -c 'import sys; raise SystemExit(0 if sys.version_info >= (3, 10) else 1)' *> $null
        if ($LASTEXITCODE -ne 0) { continue }
        $launchArguments = @($choice.Prefix + @('"' + $handler + '"', 'hook') + $Arguments)
        $process = Start-Process -FilePath $command.Source -ArgumentList $launchArguments -NoNewWindow -Wait -PassThru -RedirectStandardInput $payloadFile
        exit $process.ExitCode
    }
} finally {
    Remove-Item -LiteralPath $payloadFile -Force -ErrorAction SilentlyContinue
}
[Console]::Error.WriteLine('Lemmings runtime is active but Python 3.10 or newer is unavailable; deactivate only after a safe handoff.')
exit 1
