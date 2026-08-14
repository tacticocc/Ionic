[CmdletBinding()]
param(
  [Parameter(Mandatory = $true)]
  [string]$AppRoot,

  [string]$InstallerPath,

  [Parameter(Mandatory = $true)]
  [string]$ExpectedProductName,

  [Parameter(Mandatory = $true)]
  [string]$ExpectedPublisherPattern
)

$ErrorActionPreference = "Stop"
$resolvedRoot = (Resolve-Path -LiteralPath $AppRoot).Path
if (-not (Test-Path -LiteralPath $resolvedRoot -PathType Container)) {
  throw "The packaged app root does not exist."
}

$mainExecutable = Join-Path $resolvedRoot "$ExpectedProductName.exe"
$engineExecutable = Join-Path $resolvedRoot "resources\ionic\ionic.exe"
$resolvedInstaller = if ([string]::IsNullOrWhiteSpace($InstallerPath)) {
  $candidates = @(Get-ChildItem -LiteralPath (Split-Path -Parent $resolvedRoot) -File -Filter "Ionic-Essential-Setup-*.exe")
  if ($candidates.Count -ne 1) {
    throw "Expected exactly one Ionic Essential Setup executable beside the unpacked app; found $($candidates.Count)."
  }
  $candidates[0].FullName
} else {
  (Resolve-Path -LiteralPath $InstallerPath).Path
}
$targets = @($resolvedInstaller, $mainExecutable, $engineExecutable)

if (Get-ChildItem -LiteralPath $resolvedRoot -Recurse -File -Filter "elevate.exe") {
  throw "The release contains the disallowed elevate.exe helper."
}

$results = foreach ($target in $targets) {
  if (-not (Test-Path -LiteralPath $target -PathType Leaf)) {
    throw "Required signed executable is missing: $target"
  }
  $signature = Get-AuthenticodeSignature -LiteralPath $target
  if ($signature.Status -ne [System.Management.Automation.SignatureStatus]::Valid) {
    throw "Authenticode verification failed for $target ($($signature.Status))."
  }
  if (-not $signature.SignerCertificate -or
      $signature.SignerCertificate.Subject -notmatch $ExpectedPublisherPattern) {
    throw "The signer for $target does not match the expected publisher."
  }
  if (-not $signature.TimeStamperCertificate) {
    throw "The signature for $target has no trusted RFC 3161 or Authenticode timestamp."
  }
  [pscustomobject]@{
    file = if ($target.StartsWith($resolvedRoot, [StringComparison]::OrdinalIgnoreCase)) {
      $target.Substring($resolvedRoot.Length).TrimStart("\")
    } else {
      [IO.Path]::GetFileName($target)
    }
    status = $signature.Status.ToString()
    signer = $signature.SignerCertificate.Subject
    thumbprint = $signature.SignerCertificate.Thumbprint
    timestampSigner = $signature.TimeStamperCertificate.Subject
    timestampThumbprint = $signature.TimeStamperCertificate.Thumbprint
    timestampNotBefore = $signature.TimeStamperCertificate.NotBefore.ToUniversalTime().ToString("o")
    timestampNotAfter = $signature.TimeStamperCertificate.NotAfter.ToUniversalTime().ToString("o")
  }
}

$results | ConvertTo-Json -Depth 3
