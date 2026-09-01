param(
    [string]$Destination = (Join-Path $env:LOCALAPPDATA 'NOFXDiscord\accounts.dpapi')
)

$ErrorActionPreference = 'Stop'
$inputLines = [System.Collections.Generic.List[string]]::new()
while ($true) {
    $line = [Console]::In.ReadLine()
    if ($null -eq $line -or $line -eq '__END__') { break }
    if (-not [string]::IsNullOrWhiteSpace($line)) { $inputLines.Add($line) }
}
$lines = @($inputLines)
if ($lines.Count -eq 0) {
    throw 'No account records were supplied.'
}

$accounts = @()
for ($index = 0; $index -lt $lines.Count; $index++) {
    $normalized = $lines[$index].Trim() -replace '\\([@_.])', '$1'
    $parts = $normalized.Split(':', 3)
    if ($parts.Count -ne 3 -or [string]::IsNullOrWhiteSpace($parts[2])) {
        throw "Invalid account record at slot $($index + 1)."
    }
    $accounts += [ordered]@{
        slot = $index + 1
        token = $parts[2]
    }
}

$directory = Split-Path -Parent $Destination
New-Item -ItemType Directory -Force -Path $directory | Out-Null
$json = ConvertTo-Json -Compress -Depth 4 -InputObject @($accounts)
$encrypted = ConvertTo-SecureString $json -AsPlainText -Force | ConvertFrom-SecureString
[System.IO.File]::WriteAllText($Destination, $encrypted, [System.Text.UTF8Encoding]::new($false))

Write-Output "IMPORTED_ACCOUNT_COUNT=$($accounts.Count)"
Write-Output "ENCRYPTED_STORE=$Destination"
