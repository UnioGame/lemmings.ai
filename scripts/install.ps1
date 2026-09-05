[CmdletBinding()]
param(
    [string]$Repo,
    [string]$Project,
    [switch]$DryRun
)

$ErrorActionPreference = 'Stop'
$installer = Join-Path $PSScriptRoot '../skills/lemmings/scripts/install.py'
$choices = @(
    @{ Command = 'py'; Prefix = @('-3') },
    @{ Command = 'python3'; Prefix = @() },
    @{ Command = 'python'; Prefix = @() }
)
$selected = $null
foreach ($choice in $choices) {
    $command = Get-Command $choice.Command -ErrorAction SilentlyContinue
    if (-not $command) { continue }
    & $command.Source @($choice.Prefix) -c 'import sys; raise SystemExit(0 if sys.version_info >= (3, 10) else 1)'
    if ($LASTEXITCODE -eq 0) { $selected = @{ Path = $command.Source; Prefix = $choice.Prefix }; break }
}
if (-not $selected) { throw 'Lemmings 4.0 requires Python 3.10 or newer.' }

$arguments = @($selected.Prefix + $installer)
if ($Repo) { $arguments += @('--repo', $Repo) }
if ($Project) { $arguments += @('--project', $Project) }
if ($DryRun) { $arguments += '--dry-run' }
& $selected.Path @arguments
exit $LASTEXITCODE
