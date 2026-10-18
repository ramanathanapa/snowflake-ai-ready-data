# Bootstrap Snowflake infra: key pair -> tfvars -> terraform init/plan (-Apply to apply).
# Usage: ./scripts/bootstrap.ps1 -Org <ORG> -Account <ACCOUNT> -User <USER> [-Apply]
param(
  [Parameter(Mandatory)][string]$Org,
  [Parameter(Mandatory)][string]$Account,
  [Parameter(Mandatory)][string]$User,
  [switch]$Apply
)
$ErrorActionPreference = 'Stop'
$root = Split-Path $PSScriptRoot
$tf   = Join-Path $root 'infra/terraform'
$keyDir = Join-Path $HOME '.snowflake'
$priv = Join-Path $keyDir 'rsa_key.p8'
$pub  = Join-Path $keyDir 'rsa_key.pub'

if (-not (Get-Command terraform -ErrorAction SilentlyContinue)) {
  throw 'terraform not found. Install: winget install Hashicorp.Terraform (then open a new shell)'
}

# 1. key pair (idempotent; private key never leaves this machine)
if (-not (Test-Path $priv)) {
  $ossl = (Get-Command openssl -ErrorAction SilentlyContinue).Source
  if (-not $ossl) { $ossl = 'C:\Program Files\Git\usr\bin\openssl.exe' }
  if (-not (Test-Path $ossl)) { throw 'openssl not found (install Git for Windows)' }
  New-Item -ItemType Directory -Force $keyDir | Out-Null
  & $ossl genrsa -out "$keyDir/rsa_raw.pem" 2048 2>$null
  & $ossl pkcs8 -topk8 -inform PEM -in "$keyDir/rsa_raw.pem" -out $priv -nocrypt
  & $ossl rsa -in $priv -pubout -out $pub 2>$null
  Remove-Item "$keyDir/rsa_raw.pem"
}

# 2. MANUAL STEP: Snowflake can't be told about the key without an authenticated session
$body = (Get-Content $pub | Where-Object { $_ -notmatch '-----' }) -join ''
Write-Host "`nRun ONCE in a Snowsight worksheet (skip if already done):" -ForegroundColor Yellow
Write-Host "ALTER USER $User SET RSA_PUBLIC_KEY='$body';`n"

# 3. tfvars (never overwrites)
$vars = Join-Path $tf 'terraform.tfvars'
if (-not (Test-Path $vars)) {
  @"
organization_name = "$Org"
account_name      = "$Account"
user              = "$User"
private_key_path  = "$($priv -replace '\\','/')"
"@ | Set-Content $vars
}

# 4. terraform
Push-Location $tf
try {
  terraform init -input=false
  if ($Apply) { terraform apply -input=false } else { terraform plan -input=false }
} finally { Pop-Location }
