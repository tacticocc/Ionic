param(
    [Parameter(Mandatory = $true)]
    [string]$InstallerPattern,
    [Parameter(Mandatory = $true)]
    [string]$AppRoot
)

$ErrorActionPreference = "Stop"

$packageVersion = (Get-Content -LiteralPath "package.json" -Raw | ConvertFrom-Json).version
$resolvedInstallerPattern = $InstallerPattern.Replace("{version}", $packageVersion)
$installers = @(Get-ChildItem -Path $resolvedInstallerPattern -File)
if ($installers.Count -ne 1) {
    throw "Expected exactly one NSIS installer matching '$resolvedInstallerPattern'; found $($installers.Count)."
}

$sevenZipCommand = Get-Command 7z.exe -ErrorAction SilentlyContinue
if ($null -eq $sevenZipCommand) {
    $sevenZipCandidate = Join-Path $env:ProgramFiles "7-Zip\7z.exe"
    if (-not (Test-Path -LiteralPath $sevenZipCandidate -PathType Leaf)) {
        throw "7-Zip is required to inspect the compiled NSIS installer."
    }
    $sevenZipPath = $sevenZipCandidate
} else {
    $sevenZipPath = $sevenZipCommand.Source
}

$installer = $installers[0]
$listing = (& $sevenZipPath l -tNsis $installer.FullName 2>&1) -join "`n"
if ($LASTEXITCODE -ne 0) {
    throw "7-Zip could not inspect '$($installer.FullName)'."
}
if ($listing -match "(?i)WinShell\.dll") {
    throw "Compiled Setup unexpectedly embeds WinShell.dll."
}

$expectedPlugins = [ordered]@{
    "System.dll"   = "3EB38AE99653A7DBC724132EE240F6E5C4AF4BFE7C01D31D23FAF373F9F2EACA"
    "StdUtils.dll" = "B72E9013A6204E9F01076DC38DABBF30870D44DFC66962ADBF73619D4331601E"
    "UAC.dll"      = "2F7F8FC05DC4FD0D5CDA501B47E4433357E887BBFED7292C028D99C73B52DC08"
    "nsDialogs.dll" = "1E40211AF65923C2F4FD02CE021458A7745D28E2F383835E3015E96575632172"
    "nsExec.dll"    = "5D9CEB1CE5F35AEA5F9E5A0C0EDEEEC04DFEFE0C77890C80C70E98209B58B962"
    "nsis7z.dll"   = "B393F05E8FF919EF071181050E1873C9A776E1A0AE8329AEFFF7007D0CADF592"
}

$auditRoot = Join-Path ([System.IO.Path]::GetTempPath()) ("ionic-nsis-audit-" + [guid]::NewGuid().ToString("N"))
New-Item -ItemType Directory -Path $auditRoot | Out-Null
try {
    & $sevenZipPath e -tNsis -y ("-o" + $auditRoot) $installer.FullName '$PLUGINSDIR\*.dll' | Out-Null
    if ($LASTEXITCODE -ne 0) {
        throw "7-Zip could not extract the NSIS plug-ins for verification."
    }
    if (Test-Path -LiteralPath (Join-Path $auditRoot "WinShell.dll") -PathType Leaf) {
        throw "Compiled Setup unexpectedly embeds WinShell.dll."
    }
    $actualPluginNames = @(
        Get-ChildItem -LiteralPath $auditRoot -File -Filter "*.dll" |
            ForEach-Object { $_.Name } |
            Sort-Object
    )
    $expectedPluginNames = @($expectedPlugins.Keys | Sort-Object)
    $unexpectedPluginNames = @(Compare-Object -ReferenceObject $expectedPluginNames -DifferenceObject $actualPluginNames)
    if ($unexpectedPluginNames.Count -ne 0) {
        throw "Compiled Setup plug-in set differs from the exact reviewed allowlist."
    }
    foreach ($entry in $expectedPlugins.GetEnumerator()) {
        $pluginPath = Join-Path $auditRoot $entry.Key
        if (-not (Test-Path -LiteralPath $pluginPath -PathType Leaf)) {
            throw "Compiled Setup is missing reviewed plug-in '$($entry.Key)'."
        }
        $actual = (Get-FileHash -LiteralPath $pluginPath -Algorithm SHA256).Hash
        if ($actual -ne $entry.Value) {
            throw "Compiled Setup plug-in '$($entry.Key)' has unexpected SHA-256 '$actual'."
        }
    }
} finally {
    $resolvedAuditRoot = [System.IO.Path]::GetFullPath($auditRoot)
    $resolvedTempRoot = [System.IO.Path]::GetFullPath([System.IO.Path]::GetTempPath())
    if ($resolvedAuditRoot.StartsWith($resolvedTempRoot, [System.StringComparison]::OrdinalIgnoreCase)) {
        Remove-Item -LiteralPath $resolvedAuditRoot -Recurse -Force
    }
}

$sourceRoot = Join-Path $AppRoot "resources\legal\sources\NSIS-plugins"
$expectedSources = [ordered]@{
    "7z1900-src.7z"                         = "9BA70A5E8485CF9061B30A2A84FE741DE5AEB8DD271AAB8889DA0E9B3BF1868E"
    "nsis-3.04-src.tar.bz2"                 = "609536046C50F35CFD909DD7DF2AB38F2E835D0DA3C1048AA0D48C59C5A4F4F5"
    "Nsis7z-19.00-source-and-binaries.7z"   = "6F2F3730049926F40442EE0C8B7D3E3DEE7ACE544D82467FF8059EA3F4201C58"
    "StdUtils-1.14-sources.tar"              = "DB9F98D7A947D5A6B7CD341E01EDD412EA04510C5FAEE19A23B1E84582D86121"
    "UAC-0.2.4c-source-and-binaries.zip"     = "20E3192AF5598568887C16D88DE59A52C2CE4A26E42C5FB8BEE8105DCBBD1760"
}
foreach ($entry in $expectedSources.GetEnumerator()) {
    $sourcePath = Join-Path $sourceRoot $entry.Key
    if (-not (Test-Path -LiteralPath $sourcePath -PathType Leaf)) {
        throw "Packaged corresponding source is missing '$($entry.Key)'."
    }
    $actual = (Get-FileHash -LiteralPath $sourcePath -Algorithm SHA256).Hash
    if ($actual -ne $entry.Value) {
        throw "Packaged source '$($entry.Key)' has unexpected SHA-256 '$actual'."
    }
}

$licenseRoot = Join-Path $AppRoot "resources\legal\licenses\NSIS-plugins"
$expectedLicenses = [ordered]@{
    "LGPL-2.1.txt"                       = "1E7E6BAE5A5BDE32F1AE5A7C37A082D1AB03CF89354F7F936AC40BE9E39A6531"
    "Nsis7z-19.00-7-Zip-License.txt"     = "8B726D14F08A7FE230EE17D26862712C1BF9A34FBCA8D97609EC951148E9E27D"
    "Nsis7z-19.00-LZMA-SDK-License.txt"  = "63FCFE54826809B5A9F9DF8A8FF53C04330122319EC67A2DD815085586ADAF0E"
    "PROVENANCE.txt"                     = "9B6CD573E83487A97F1C32BB8B384F0082676E8347A38F29BF580CAAB216FA6B"
    "StdUtils-1.14-CLARIFICATION.txt"    = "AE0AB3F49B4473152181C1FE5670435FAACA079EDA0B261EE31A51A6180BC9E7"
    "UAC-0.2.4c-License.txt"             = "997FD014643F27A2F240FAF9B9B54A98371775C63EB38134829154794F7326FA"
}
foreach ($entry in $expectedLicenses.GetEnumerator()) {
    $licensePath = Join-Path $licenseRoot $entry.Key
    if (-not (Test-Path -LiteralPath $licensePath -PathType Leaf)) {
        throw "Packaged NSIS legal asset is missing '$($entry.Key)'."
    }
    $actual = (Get-FileHash -LiteralPath $licensePath -Algorithm SHA256).Hash
    if ($actual -ne $entry.Value) {
        throw "Packaged NSIS legal asset '$($entry.Key)' has unexpected SHA-256 '$actual'."
    }
}

Write-Host "Verified NSIS contents and corresponding-source assets: $($installer.FullName)"
