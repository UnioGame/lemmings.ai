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
        $content = [IO.File]::ReadAllText($_.FullName).Replace("`r`n", "`n")
        $hasher = [Security.Cryptography.SHA256]::Create()
        try { $hash = -join ($hasher.ComputeHash([Text.Encoding]::UTF8.GetBytes($content)) | ForEach-Object { $_.ToString('x2') }) }
        finally { $hasher.Dispose() }
        '{0}:{1}' -f (Get-RelativeSlashPath -From $Root -To $_.FullName), $hash
    }
    return ($lines -join "`n")
}

function Test-SchemaVersion {
    param([Parameter(Mandatory = $true)][string]$Path, [Parameter(Mandatory = $true)][int]$Version)
    if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) { return $false }
    try { return ([IO.File]::ReadAllText($Path) | ConvertFrom-Json).schemaVersion -eq $Version }
    catch { return $false }
}

$packageRoot = Resolve-FullPath (Join-Path $PSScriptRoot '..')
if (($packageRoot -replace '\\', '/') -match '(?i)(?:^|/)(?:Library/PackageCache)(?:/|$)') {
    throw "Refusing to install from Unity's Library/PackageCache: $packageRoot"
}
$skillSource = Join-Path $packageRoot 'skills/lemmings'
$agentsSource = Join-Path $packageRoot 'agents'
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
$commonDir = Invoke-Git -At $repoRoot -Arguments @('rev-parse', '--git-common-dir')
if (-not $commonDir) { throw "Cannot resolve Git common directory for '$repoRoot'." }
$commonPath = Resolve-FullPath -Path $commonDir -Base $repoRoot
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

$repoName = Split-Path -Leaf $repoRoot
$projectRelative = Get-RelativeSlashPath -From $repoRoot -To $projectPath
$profileDefaults = [pscustomobject]@{
    schemaVersion = 3
    distributionVersion = '3.2.0'
    mode = 'auto'
    roadmap = 'docs/tasks/ROADMAP.md'
    taskGlobs = @('docs/tasks/**/*.json')
    reviewGlobs = @('docs/tasks/reviews/*.json')
    worktreeRoot = '../lemmings-worktrees'
    modelRoutes = [pscustomobject]@{
        codex = [pscustomobject]@{
            worker = @(
                [pscustomobject]@{ providerId = 'openai'; modelId = 'gpt-5.6-luna'; variantId = 'max'; specializations = @('default') },
                [pscustomobject]@{ providerId = 'openai'; modelId = 'gpt-5.6-terra'; variantId = 'max'; specializations = @('default') }
            )
            reviewer = @([pscustomobject]@{ providerId = 'openai'; modelId = 'gpt-5.6-sol'; variantId = 'high'; specializations = @('default') })
            explorer = @([pscustomobject]@{ providerId = 'openai'; modelId = 'gpt-5.6-luna'; variantId = 'high'; specializations = @('default') })
        }
    }
    contextPolicy = [pscustomobject]@{ maxPacketBytes = 16384; maxWorkingSetItems = 12; maxExpansions = 1 }
    orchestration = [pscustomobject]@{ maxDelegationDepth = 1; maxConcurrentWriters = 2; maxConcurrentReaders = 2; managerSlots = 1; maxRepairs = 1; maxTransportRetries = 1 }
    workspacePool = [pscustomobject]@{ enabled = $true; maxIdle = 2; maxIdleGiB = 10; eviction = 'lru' }
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
$profilePath = Join-Path $repoRoot '.agents/lemmings.json'
$legacyProfilePath = Join-Path $repoRoot '.codex/lemmings.json'
$activeMarkerPath = Join-Path $commonPath 'lemmings/active.json'
$environmentPath = Join-Path $commonPath 'lemmings/environment.json'
$profileJson = $profileDefaults | ConvertTo-Json -Depth 20
$skillTarget = Join-Path $repoRoot '.agents/skills/lemmings'
$agentsTarget = Join-Path $repoRoot '.codex/agents'
$sourceAgentFiles = @(Get-ChildItem -LiteralPath $agentsSource -Filter 'lemmings-*.toml' -File)
$currentAgentNames = @('lemmings-worker.toml', 'lemmings-reviewer.toml', 'lemmings-explorer.toml')
$obsoleteAgentNames = @('lemmings-orchestrator.toml', 'lemmings-validator.toml', 'lemmings-summarizer.toml')
$ownedAgentNames = @($currentAgentNames + $obsoleteAgentNames)
$installedAgentFiles = @($ownedAgentNames | ForEach-Object { $path = Join-Path $agentsTarget $_; if (Test-Path -LiteralPath $path -PathType Leaf) { Get-Item -LiteralPath $path } })
$currentProfileV3 = Test-SchemaVersion -Path $profilePath -Version 3
$legacyRecognized = (-not $currentProfileV3) -and (
    (Test-SchemaVersion -Path $legacyProfilePath -Version 2) -or
    (Test-SchemaVersion -Path $activeMarkerPath -Version 2) -or
    [bool](@($obsoleteAgentNames | Where-Object { Test-Path -LiteralPath (Join-Path $agentsTarget $_) }).Count)
)
$skillDrift = (Test-Path -LiteralPath $skillTarget) -and ((Get-TreeFingerprint $skillSource) -ne (Get-TreeFingerprint $skillTarget))
$agentDrift = $false
if ($installedAgentFiles.Count -gt 0) {
    $installedCurrent = @($installedAgentFiles | Where-Object { $_.Name -in $currentAgentNames })
    $agentDrift = (($sourceAgentFiles.Name | Sort-Object) -join '|') -ne (($installedCurrent.Name | Sort-Object) -join '|') -or @($installedAgentFiles | Where-Object { $_.Name -in $obsoleteAgentNames }).Count -gt 0
    foreach ($sourceAgent in $sourceAgentFiles) {
        $targetAgent = Join-Path $agentsTarget $sourceAgent.Name
        if (-not (Test-Path -LiteralPath $targetAgent) -or (Get-FileHash $sourceAgent.FullName).Hash -ne (Get-FileHash $targetAgent).Hash) { $agentDrift = $true }
    }
}
$configDrift = (Test-Path -LiteralPath $profilePath) -and ([IO.File]::ReadAllText($profilePath).Trim() -ne $profileJson.Trim())
$legacyTargetsPresent = (Test-Path -LiteralPath $legacyProfilePath) -or (Test-SchemaVersion -Path $activeMarkerPath -Version 2) -or @($installedAgentFiles | Where-Object { $_.Name -in $obsoleteAgentNames }).Count -gt 0
$bundlePresent = (Test-Path -LiteralPath $skillTarget) -or $installedAgentFiles.Count -gt 0 -or (Test-Path -LiteralPath $profilePath) -or $legacyTargetsPresent
$bundleDrift = $skillDrift -or $agentDrift -or $configDrift -or ($bundlePresent -and ((-not (Test-Path -LiteralPath $skillTarget)) -or $installedAgentFiles.Count -eq 0 -or (-not (Test-Path -LiteralPath $profilePath))))
if ($bundleDrift -and -not $Force -and -not $legacyRecognized) { throw 'Lemmings bundle differs from the canonical 3.2 distribution. Re-run with -Force to replace it.' }
$environmentNeedsChange = if ($packageInsideRepo) {
    (Test-SchemaVersion -Path $environmentPath -Version 2) -or (Test-SchemaVersion -Path $environmentPath -Version 3)
} else {
    if (-not (Test-SchemaVersion -Path $environmentPath -Version 3)) { $true }
    else {
        try { ([IO.File]::ReadAllText($environmentPath) | ConvertFrom-Json).toolRoot -ne $packageRoot }
        catch { $true }
    }
}
$replacementNeeded = (-not $bundlePresent) -or $bundleDrift -or $legacyTargetsPresent -or $environmentNeedsChange -or $Force

if ($DryRun) {
    Write-Host "replace: $skillTarget"
    Write-Host "replace: $profilePath"
    foreach ($name in $currentAgentNames) { Write-Host "replace: $(Join-Path $agentsTarget $name)" }
    foreach ($path in @($legacyProfilePath) + @($obsoleteAgentNames | ForEach-Object { Join-Path $agentsTarget $_ })) {
        if (Test-Path -LiteralPath $path) { Write-Host "delete: $path" }
    }
    if (Test-SchemaVersion -Path $activeMarkerPath -Version 2) { Write-Host "delete: $activeMarkerPath" }
    Write-Host $(if ($packageInsideRepo) { "delete if Lemmings-owned: $environmentPath" } else { "replace: $environmentPath" })
}
elseif ($replacementNeeded) {
    $transaction = Join-Path $repoRoot ('.lemmings-install-' + [guid]::NewGuid().ToString('N'))
    $stage = Join-Path $transaction 'stage'
    $backup = Join-Path $transaction 'backup'
    $skillBackup = Join-Path $backup 'skill'
    $agentsBackup = Join-Path $backup 'agents'
    $configBackup = Join-Path $backup 'lemmings.json'
    $legacyConfigBackup = Join-Path $backup 'legacy-lemmings.json'
    $activeBackup = Join-Path $backup 'active.json'
    $environmentBackup = Join-Path $backup 'environment.json'
    $hadSkill = Test-Path -LiteralPath $skillTarget
    $hadConfig = Test-Path -LiteralPath $profilePath
    $hadLegacyConfig = Test-Path -LiteralPath $legacyProfilePath
    $hadActive = Test-Path -LiteralPath $activeMarkerPath
    $hadEnvironment = Test-Path -LiteralPath $environmentPath
    try {
        New-Item -ItemType Directory -Force -Path (Join-Path $stage 'skill'), (Join-Path $stage 'agents'), $backup | Out-Null
        Copy-Item -Path (Join-Path $skillSource '*') -Destination (Join-Path $stage 'skill') -Recurse -Force
        Copy-Item -LiteralPath $sourceAgentFiles.FullName -Destination (Join-Path $stage 'agents') -Force
        [IO.File]::WriteAllText((Join-Path $stage 'lemmings.json'), $profileJson + [Environment]::NewLine, [Text.UTF8Encoding]::new($false))
        if ($hadSkill) { Copy-Item -LiteralPath $skillTarget -Destination $skillBackup -Recurse -Force }
        if ($installedAgentFiles.Count -gt 0) { New-Item -ItemType Directory -Force -Path $agentsBackup | Out-Null; Copy-Item -LiteralPath $installedAgentFiles.FullName -Destination $agentsBackup -Force }
        if ($hadConfig) { Copy-Item -LiteralPath $profilePath -Destination $configBackup -Force }
        if ($hadLegacyConfig) { Copy-Item -LiteralPath $legacyProfilePath -Destination $legacyConfigBackup -Force }
        if ($hadActive) { Copy-Item -LiteralPath $activeMarkerPath -Destination $activeBackup -Force }
        if ($hadEnvironment) { Copy-Item -LiteralPath $environmentPath -Destination $environmentBackup -Force }

        Remove-Item -LiteralPath $skillTarget -Recurse -Force -ErrorAction SilentlyContinue
        New-Item -ItemType Directory -Force -Path (Split-Path -Parent $skillTarget) | Out-Null
        Move-Item -LiteralPath (Join-Path $stage 'skill') -Destination $skillTarget
        if ($env:LEMMINGS_INSTALL_FAIL_AFTER -eq 'skill') { throw 'Injected failure after skill replacement.' }
        New-Item -ItemType Directory -Force -Path $agentsTarget | Out-Null
        foreach ($name in $ownedAgentNames) { Remove-Item -LiteralPath (Join-Path $agentsTarget $name) -Force -ErrorAction SilentlyContinue }
        Copy-Item -Path (Join-Path $stage 'agents/*') -Destination $agentsTarget -Force
        if ($env:LEMMINGS_INSTALL_FAIL_AFTER -eq 'agents') { throw 'Injected failure after agent replacement.' }
        New-Item -ItemType Directory -Force -Path (Split-Path -Parent $profilePath) | Out-Null
        Remove-Item -LiteralPath $profilePath -Force -ErrorAction SilentlyContinue
        Move-Item -LiteralPath (Join-Path $stage 'lemmings.json') -Destination $profilePath -Force
        Remove-Item -LiteralPath $legacyProfilePath -Force -ErrorAction SilentlyContinue
        if (Test-SchemaVersion -Path $activeMarkerPath -Version 2) { Remove-Item -LiteralPath $activeMarkerPath -Force }
        if ($packageInsideRepo) {
            if ((Test-SchemaVersion -Path $environmentPath -Version 2) -or (Test-SchemaVersion -Path $environmentPath -Version 3)) { Remove-Item -LiteralPath $environmentPath -Force }
        }
        else {
            New-Item -ItemType Directory -Force -Path (Split-Path -Parent $environmentPath) | Out-Null
            [IO.File]::WriteAllText($environmentPath, (([pscustomobject]@{ schemaVersion = 3; toolRoot = $packageRoot } | ConvertTo-Json -Compress) + [Environment]::NewLine), [Text.UTF8Encoding]::new($false))
        }
        if ($env:LEMMINGS_INSTALL_FAIL_AFTER -eq 'config') { throw 'Injected failure after config replacement.' }

        if ((Get-TreeFingerprint $skillSource) -ne (Get-TreeFingerprint $skillTarget)) { throw 'Installed skill failed distribution validation.' }
        if ([IO.File]::ReadAllText($profilePath).Trim() -ne $profileJson.Trim()) { throw 'Installed profile failed distribution validation.' }
        foreach ($sourceAgent in $sourceAgentFiles) {
            $targetAgent = Join-Path $agentsTarget $sourceAgent.Name
            if (-not (Test-Path -LiteralPath $targetAgent) -or (Get-FileHash $sourceAgent.FullName).Hash -ne (Get-FileHash $targetAgent).Hash) { throw "Installed agent failed distribution validation: $($sourceAgent.Name)" }
        }
        if ((Test-Path -LiteralPath $legacyProfilePath) -or (Test-SchemaVersion -Path $activeMarkerPath -Version 2) -or @($obsoleteAgentNames | Where-Object { Test-Path -LiteralPath (Join-Path $agentsTarget $_) }).Count -gt 0) { throw 'Legacy Lemmings targets remain after installation.' }
    }
    catch {
        Remove-Item -LiteralPath $skillTarget -Recurse -Force -ErrorAction SilentlyContinue
        if ($hadSkill) { Move-Item -LiteralPath $skillBackup -Destination $skillTarget -Force }
        if (Test-Path -LiteralPath $agentsTarget) { foreach ($name in $ownedAgentNames) { Remove-Item -LiteralPath (Join-Path $agentsTarget $name) -Force -ErrorAction SilentlyContinue } }
        if (Test-Path -LiteralPath $agentsBackup) { New-Item -ItemType Directory -Force -Path $agentsTarget | Out-Null; Copy-Item -Path (Join-Path $agentsBackup '*') -Destination $agentsTarget -Force }
        Remove-Item -LiteralPath $profilePath -Force -ErrorAction SilentlyContinue
        if ($hadConfig) { Move-Item -LiteralPath $configBackup -Destination $profilePath -Force }
        Remove-Item -LiteralPath $legacyProfilePath -Force -ErrorAction SilentlyContinue
        if ($hadLegacyConfig) { New-Item -ItemType Directory -Force -Path (Split-Path -Parent $legacyProfilePath) | Out-Null; Move-Item -LiteralPath $legacyConfigBackup -Destination $legacyProfilePath -Force }
        Remove-Item -LiteralPath $activeMarkerPath -Force -ErrorAction SilentlyContinue
        if ($hadActive) { New-Item -ItemType Directory -Force -Path (Split-Path -Parent $activeMarkerPath) | Out-Null; Move-Item -LiteralPath $activeBackup -Destination $activeMarkerPath -Force }
        Remove-Item -LiteralPath $environmentPath -Force -ErrorAction SilentlyContinue
        if ($hadEnvironment) { New-Item -ItemType Directory -Force -Path (Split-Path -Parent $environmentPath) | Out-Null; Move-Item -LiteralPath $environmentBackup -Destination $environmentPath -Force }
        throw
    }
    finally { Remove-Item -LiteralPath $transaction -Recurse -Force -ErrorAction SilentlyContinue }
}

Write-Host $(if ($DryRun) { 'Lemmings bootstrap dry run complete.' } else { 'Lemmings skill bootstrap complete.' })
