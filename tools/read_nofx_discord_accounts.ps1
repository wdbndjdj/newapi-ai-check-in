param(
    [string]$Source = (Join-Path $env:LOCALAPPDATA 'NOFXDiscord\accounts.dpapi')
)

$ErrorActionPreference = 'Stop'
$encrypted = [System.IO.File]::ReadAllText($Source, [System.Text.Encoding]::UTF8)
$secure = ConvertTo-SecureString $encrypted
$pointer = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($secure)
try {
    [Runtime.InteropServices.Marshal]::PtrToStringBSTR($pointer)
} finally {
    [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($pointer)
}
