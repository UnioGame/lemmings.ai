[CmdletBinding()]
param(
    [Parameter()]
    [string]$Repo,

    [Parameter()]
    [string]$Project,

    [Parameter()]
    [switch]$DryRun,

    [Parameter()]
    [switch]$Force
)

$ErrorActionPreference = 'Stop'

function Resolve-FullPath {
    param([Parameter(Mandatory = $true)][string]$Path, [string]$Base = (Get-Location).Path)
    if ([IO.Path]::IsPathRooted($Path)) {
        return [IO.Path]::GetFullPath($Path)
    }
    return [IO.Path]::GetFullPath((Join-Path $Base $Path))
}

function Invoke-Git {
    param([Parameter(Mandatory = $true)][string]$At, [Parameter(Mandatory = $true)][string[]]$Arguments)
    $output = & git -C $At @Arguments 2>$null
    if ($LASTEXITCODE -ne 0) { return $null }
    return ($output | Out-String).Trim()
}

function Get-GitRoot {
    param([Parameter(Mandatory = $true)][string]$At)
    if (-not (Test-Path -LiteralPath $At -PathType Container)) { return $null }
    return Invoke-Git -At $At -Arguments @('rev-parse', '--show-toplevel')
}

function Test-IsWithin {
    param([Parameter(Mandatory = $true)][string]$Child, [Parameter(Mandatory = $true)][string]$Parent)
    $childPath = Resolve-FullPath $Child
    $parentPath = (Resolve-FullPath $Parent).TrimEnd([IO.Path]::DirectorySeparatorChar, [IO.Path]::AltDirectorySeparatorChar)
    return $childPath.Equals($parentPath, [StringComparison]::OrdinalIgnoreCase) -or
        $childPath.StartsWith($parentPath + [IO.Path]::DirectorySeparatorChar, [StringComparison]::OrdinalIgnoreCase)
}

function Get-RelativeSlashPath {
    param([Parameter(Mandatory = $true)][string]$From, [Parameter(Mandatory = $true)][string]$To)
    return ([IO.Path]::GetRelativePath((Resolve-FullPath $From), (Resolve-FullPath $To)) -replace '\\', '/')
}

function Test-UnityProject {
    param([Parameter(Mandatory = $true)][string]$Path)
    return (Test-Path -LiteralPath (Join-Path $Path 'Assets') -PathType Container) -and
        (Test-Path -LiteralPath (Join-Path $Path 'Packages/manifest.json') -PathType Leaf) -and
        (Test-Path -LiteralPath (Join-Path $Path 'ProjectSettings/ProjectVersion.txt') -PathType Leaf)
}

function Find-UnityProjects {
    param([Parameter(Mandatory = $true)][string]$Repository)
    $manifests = Get-ChildItem -LiteralPath $Repository -Filter 'manifest.json' -File -Recurse -ErrorAction SilentlyContinue |
        Where-Object {
            $_.Directory.Name -eq 'Packages' -and
            $_.FullName -notmatch '[\\/](?:\.git|Library|PackageCache)[\\/]'
        }
    $projects = foreach ($manifest in $manifests) {
        $candidate = $manifest.Directory.Parent.FullName
        if (Test-UnityProject $candidate) { Resolve-FullPath $candidate }
    }
    return @($projects | Sort-Object -Unique)
}

function Get-TreeFingerprint {
    param([Parameter(Mandatory = $true)][string]$Root)
    if (-not (Test-Path -LiteralPath $Root -PathType Container)) { return $null }
    $lines = Get-ChildItem -LiteralPath $Root -File -Recurse | Sort-Object FullName | ForEach-Object {
        '{0}:{1}' -f (Get-RelativeSlashPath -From $Root -To $_.FullName), (Get-FileHash -LiteralPath $_.FullName -Algorithm SHA256).Hash
    }
    return ($lines -join "`n")
}

function Merge-MissingProperties {
    param([Parameter(Mandatory = $true)]$Target, [Parameter(Mandatory = $true)]$Defaults)
    foreach ($property in $Defaults.PSObject.Properties) {
        $existing = $Target.PSObject.Properties[$property.Name]
        if ($null -eq $existing) {
            $Target | Add-Member -NotePropertyName $property.Name -NotePropertyValue $property.Value
        }
        elseif ($existing.Value -is [pscustomobject] -and $property.Value -is [pscustomobject]) {
            Merge-MissingProperties -Target $existing.Value -Defaults $property.Value
        }
    }
}

function Write-JsonMerged {
    param(
        [Parameter(Mandatory = $true)][string]$Path,
        [Parameter(Mandatory = $true)]$Defaults,
        [switch]$SetToolRoot
    )
    if (Test-Path -LiteralPath $Path -PathType Leaf) {
        try { $value = Get-Content -LiteralPath $Path -Raw | ConvertFrom-Json }
        catch { throw "Cannot merge invalid JSON file '$Path': $($_.Exception.Message)" }
        if ($value -isnot [pscustomobject]) { throw "JSON file '$Path' must contain an object." }
    }
    else {
        $value = [pscustomobject]@{}
    }
    Merge-MissingProperties -Target $value -Defaults $Defaults
    if ($SetToolRoot) {
        if ($null -eq $value.PSObject.Properties['toolRoot']) {
            $value | Add-Member -NotePropertyName 'toolRoot' -NotePropertyValue $Defaults.toolRoot
        }
        else { $value.toolRoot = $Defaults.toolRoot }
    }
    $json = $value | ConvertTo-Json -Depth 32
    if (Test-Path -LiteralPath $Path -PathType Leaf) {
        $current = (Get-Content -LiteralPath $Path -Raw).TrimEnd()
        if ($current -eq $json.TrimEnd()) { return }
    }
    Write-Host "write $Path"
    if (-not $DryRun) {
        New-Item -ItemType Directory -Force -Path (Split-Path -Parent $Path) | Out-Null
        [IO.File]::WriteAllText($Path, $json + [Environment]::NewLine, [Text.UTF8Encoding]::new($false))
    }
}

$packageRoot = Resolve-FullPath (Join-Path $PSScriptRoot '..')
if (($packageRoot -replace '\\', '/') -match '(?i)(?:^|/)(?:Library/PackageCache)(?:/|$)') {
    throw "Refusing to install from Unity's Library/PackageCache: $packageRoot"
}
$skillSource = Join-Path $packageRoot 'skills/lemmings'
if (-not (Test-Path -LiteralPath (Join-Path $skillSource 'SKILL.md') -PathType Leaf)) {
    throw "Lemmings skill source is missing: $skillSource"
}

if ($Repo) {
    $repoRoot = Get-GitRoot (Resolve-FullPath $Repo)
}
else {
    $superproject = Invoke-Git -At $packageRoot -Arguments @('rev-parse', '--show-superproject-working-tree')
    $packageGitRoot = Get-GitRoot $packageRoot
    $cwdGitRoot = Get-GitRoot (Get-Location).Path
    if ($superproject) { $repoRoot = Resolve-FullPath $superproject }
    elseif ($packageGitRoot -and -not (Resolve-FullPath $packageGitRoot).Equals($packageRoot, [StringComparison]::OrdinalIgnoreCase)) { $repoRoot = Resolve-FullPath $packageGitRoot }
    elseif ($cwdGitRoot -and (-not $packageGitRoot -or -not (Resolve-FullPath $cwdGitRoot).Equals((Resolve-FullPath $packageGitRoot), [StringComparison]::OrdinalIgnoreCase))) { $repoRoot = Resolve-FullPath $cwdGitRoot }
    else { $repoRoot = $packageGitRoot }
}
if (-not $repoRoot) { throw 'Cannot infer a consumer Git repository. Pass -Repo <path>.' }
$repoRoot = Resolve-FullPath $repoRoot
$existingProfile = Join-Path $repoRoot '.codex/lemmings.json'
if ((Test-Path -LiteralPath $existingProfile -PathType Leaf) -and (Select-String -LiteralPath $existingProfile -Pattern '"complex-worker"\s*:' -Quiet)) {
    throw "Unsupported legacy model role 'complex-worker' in $existingProfile. Remove it and use 'worker' before bootstrapping schema version 1."
}

if ($Project) {
    $projectPath = Resolve-FullPath -Path $Project -Base $repoRoot
    if (-not (Test-UnityProject $projectPath)) { throw "Not a Unity game project (required: Assets, Packages/manifest.json, ProjectSettings/ProjectVersion.txt): $projectPath" }
}
else {
    $projects = @(Find-UnityProjects $repoRoot)
    if ($projects.Count -eq 0) { throw "No Unity game project found under '$repoRoot'. Pass -Project <path>." }
    if ($projects.Count -gt 1) { throw "Multiple Unity game projects found under '$repoRoot'. Pass -Project <path>." }
    $projectPath = $projects[0]
}
if (-not (Test-IsWithin -Child $projectPath -Parent $repoRoot)) { throw "Unity project must be inside the consumer repository: $projectPath" }

$skillTarget = Join-Path $repoRoot '.agents/skills/lemmings'
$sourceFingerprint = Get-TreeFingerprint $skillSource
$targetFingerprint = Get-TreeFingerprint $skillTarget
if ($null -eq $targetFingerprint) {
    Write-Host "copy $skillSource -> $skillTarget"
    if (-not $DryRun) {
        New-Item -ItemType Directory -Force -Path $skillTarget | Out-Null
        Copy-Item -Path (Join-Path $skillSource '*') -Destination $skillTarget -Recurse -Force
    }
}
elseif ($sourceFingerprint -ne $targetFingerprint) {
    if (-not $Force) { throw "Skill target differs from the package copy: $skillTarget. Re-run with -Force to replace it." }
    Write-Host "replace $skillTarget"
    if (-not $DryRun) {
        Remove-Item -LiteralPath $skillTarget -Recurse -Force
        New-Item -ItemType Directory -Force -Path $skillTarget | Out-Null
        Copy-Item -Path (Join-Path $skillSource '*') -Destination $skillTarget -Recurse -Force
    }
}

$repoName = Split-Path -Leaf $repoRoot
$projectRelative = Get-RelativeSlashPath -From $repoRoot -To $projectPath
$profileDefaults = [pscustomobject]@{
    schemaVersion = 1
    mode = 'auto'
    roadmap = 'docs/tasks/ROADMAP.md'
    taskGlobs = @('docs/tasks/**/*.json')
    reviewGlobs = @('docs/tasks/reviews/*.json')
    worktreeRoot = '../lemmings-worktrees'
    models = [pscustomobject]@{
        orchestrator = 'gpt-5.6-sol:high'
        reviewer = 'gpt-5.6-sol:high'
        worker = 'gpt-5.6-luna:max'
        validator = 'gpt-5.6-terra:medium'
    }
    workerPolicy = [pscustomobject]@{
        elevatedModel = 'gpt-5.6-terra:max'
        highRiskModel = 'gpt-5.6-sol:medium'
    }
    fallback = [pscustomobject]@{ allowed = @() }
    game = [pscustomobject]@{
        engine = 'unity'
        projectPath = $projectRelative
        workspace = [pscustomobject]@{
            policy = 'auto'
            parallelStrategy = 'hybrid'
            largeThresholdGiB = 10
            validationBackend = 'clone'
            validationPath = "../$repoName.lemmings.validation"
            maxUnityEditors = 1
        }
    }
}

$packageInsideRepo = Test-IsWithin -Child $packageRoot -Parent $repoRoot
if ($packageInsideRepo) {
    $profileDefaults | Add-Member -NotePropertyName 'tooling' -NotePropertyValue ([pscustomobject]@{
        root = Get-RelativeSlashPath -From $repoRoot -To $packageRoot
    })
}
Write-JsonMerged -Path (Join-Path $repoRoot '.codex/lemmings.json') -Defaults $profileDefaults

if (-not $packageInsideRepo) {
    $commonDir = Invoke-Git -At $repoRoot -Arguments @('rev-parse', '--git-common-dir')
    if (-not $commonDir) { throw "Cannot resolve Git common directory for '$repoRoot'." }
    $commonPath = Resolve-FullPath -Path $commonDir -Base $repoRoot
    Write-JsonMerged -Path (Join-Path $commonPath 'lemmings/environment.json') -Defaults ([pscustomobject]@{ schemaVersion = 1; toolRoot = $packageRoot }) -SetToolRoot
}

Write-Host $(if ($DryRun) { 'Lemmings bootstrap dry run complete.' } else { 'Lemmings skill bootstrap complete.' })
