# Native Windows entry point. compass.cmd supplies a process-local script policy.
# No administrator session, global tools, or shell-profile changes are needed.
$ErrorActionPreference = 'Stop'
$compassArguments = $args
$root = Split-Path -Parent $PSScriptRoot
Set-Location -LiteralPath $root
$env:PYTHONUTF8 = '1'

try {
    if (-not (Get-Command git.exe -CommandType Application -ErrorAction SilentlyContinue)) {
        throw 'Compass needs Git for Windows installed. Install Git, reopen this terminal, and run .\compass again.'
    }

    $cache = Join-Path $root '.compass'
    if (-not (Test-Path -LiteralPath $cache)) {
        $null = New-Item -ItemType Directory -Path $cache
        $sid = [System.Security.Principal.WindowsIdentity]::GetCurrent().User
        $acl = New-Object System.Security.AccessControl.DirectorySecurity
        $acl.SetOwner($sid)
        $acl.SetAccessRuleProtection($true, $false)
        $rule = New-Object System.Security.AccessControl.FileSystemAccessRule(
            $sid, 'FullControl', 'ContainerInherit,ObjectInherit', 'None', 'Allow'
        )
        $acl.AddAccessRule($rule)
        Set-Acl -LiteralPath $cache -AclObject $acl
    }
    $tools = Join-Path $cache 'tools'
    $null = New-Item -ItemType Directory -Path $tools -Force
    $existing = Get-Command uv.exe -CommandType Application -ErrorAction SilentlyContinue
    $uv = Join-Path $tools 'uv.exe'
    if ($existing) {
        $uv = $existing.Source
    } elseif (-not (Test-Path -LiteralPath $uv)) {
        Write-Host 'Preparing Compass: installing uv locally...'
        $installer = Join-Path $cache ('uv-install-' + [guid]::NewGuid().ToString('N') + '.ps1')
        $previousInstall = $env:UV_UNMANAGED_INSTALL
        try {
            [Net.ServicePointManager]::SecurityProtocol = [Net.ServicePointManager]::SecurityProtocol -bor [Net.SecurityProtocolType]::Tls12
            Invoke-WebRequest -UseBasicParsing -Uri 'https://astral.sh/uv/0.10.5/install.ps1' -OutFile $installer
            $env:UV_UNMANAGED_INSTALL = $tools
            & powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -File $installer
            if ($LASTEXITCODE -ne 0 -or -not (Test-Path -LiteralPath $uv)) {
                throw 'The local uv installation failed. Run .\compass again to retry.'
            }
        } finally {
            $env:UV_UNMANAGED_INSTALL = $previousInstall
            Remove-Item -LiteralPath $installer -Force -ErrorAction SilentlyContinue
        }
    }

    $env:COMPASS_UV = $uv
    $env:UV_PROJECT_ENVIRONMENT = Join-Path $cache 'dashboard-venv'
    & $uv run --frozen --no-dev --python 3.13 python -m linkedin_dashboard.launcher @compassArguments
    exit $LASTEXITCODE
} catch {
    [Console]::Error.WriteLine('Compass could not start: ' + $_.Exception.Message)
    exit 1
}
