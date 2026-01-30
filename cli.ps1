#Requires -Version 5.1
<#
.SYNOPSIS
    SC Deployer CLI wrapper for Windows PowerShell.

.DESCRIPTION
    Checks Python availability, version, and dependencies before running manage.py.

.EXAMPLE
    .\cli.ps1
    Opens interactive menu.

.EXAMPLE
    .\cli.ps1 profiles list
    Lists configured profiles.

.EXAMPLE
    .\cli.ps1 status
    Shows overall status.
#>

param(
    [Parameter(ValueFromRemainingArguments = $true)]
    [string[]]$Arguments
)

$ErrorActionPreference = "Stop"
$ScriptRoot = Split-Path -Parent $MyInvocation.MyCommand.Path

# Colors
function Write-Info { param($Message) Write-Host $Message -ForegroundColor Cyan }
function Write-Success { param($Message) Write-Host $Message -ForegroundColor Green }
function Write-Warn { param($Message) Write-Host $Message -ForegroundColor Yellow }
function Write-Err { param($Message) Write-Host $Message -ForegroundColor Red }

# Header
function Show-Header {
    Write-Host ""
    Write-Host "  SC Deployer" -ForegroundColor Cyan
    Write-Host "  ==========" -ForegroundColor DarkGray
    Write-Host ""
}

# Check Python installation
function Test-Python {
    $pythonCommands = @("python", "python3", "py")
    
    foreach ($cmd in $pythonCommands) {
        try {
            $version = & $cmd --version 2>&1
            if ($LASTEXITCODE -eq 0 -and $version -match "Python (\d+)\.(\d+)\.(\d+)") {
                $major = [int]$Matches[1]
                $minor = [int]$Matches[2]
                
                if ($major -ge 3 -and $minor -ge 10) {
                    return $cmd
                } else {
                    Write-Warn "  Found $version but Python 3.10+ is required"
                }
            }
        } catch {
            # Command not found, try next
        }
    }
    
    return $null
}

# Check virtual environment
function Test-VirtualEnv {
    $venvPaths = @(
        (Join-Path $ScriptRoot ".venv"),
        (Join-Path $ScriptRoot "venv"),
        (Join-Path $ScriptRoot ".env")
    )
    
    foreach ($venv in $venvPaths) {
        $activateScript = Join-Path $venv "Scripts\Activate.ps1"
        if (Test-Path $activateScript) {
            return $venv
        }
    }
    
    return $null
}

# Activate virtual environment
function Enable-VirtualEnv {
    param($VenvPath)
    
    $activateScript = Join-Path $VenvPath "Scripts\Activate.ps1"
    if (Test-Path $activateScript) {
        & $activateScript
        return $true
    }
    return $false
}

# Check if requirements are installed
function Test-Requirements {
    param($PythonCmd)
    
    $reqFile = Join-Path $ScriptRoot "requirements.txt"
    if (-not (Test-Path $reqFile)) {
        return $true
    }
    
    try {
        # Check key packages
        $result = & $PythonCmd -c "import boto3; import yaml; import questionary" 2>&1
        return $LASTEXITCODE -eq 0
    } catch {
        return $false
    }
}

# Install requirements
function Install-Requirements {
    param($PythonCmd)
    
    $reqFile = Join-Path $ScriptRoot "deployer\requirements.txt"
    
    Write-Info "  Installing dependencies..."
    & $PythonCmd -m pip install -q -r $reqFile
    
    if ($LASTEXITCODE -ne 0) {
        Write-Err "  Failed to install dependencies"
        return $false
    }
    
    Write-Success "  Dependencies installed"
    return $true
}

# Create virtual environment
function New-VirtualEnv {
    param($PythonCmd)
    
    $venvPath = Join-Path $ScriptRoot ".venv"
    
    Write-Info "  Creating virtual environment..."
    & $PythonCmd -m venv $venvPath
    
    if ($LASTEXITCODE -ne 0) {
        Write-Err "  Failed to create virtual environment"
        return $null
    }
    
    Write-Success "  Virtual environment created at .venv"
    return $venvPath
}

# Main
function Main {
    # Check Python
    Write-Host "  Checking Python..." -NoNewline
    $pythonCmd = Test-Python
    
    if (-not $pythonCmd) {
        Write-Err " NOT FOUND"
        Write-Host ""
        Write-Err "  Python 3.10+ is required but not found."
        Write-Host ""
        Write-Host "  Install Python from: https://www.python.org/downloads/"
        Write-Host "  Or via winget: winget install Python.Python.3.12"
        Write-Host ""
        exit 1
    }
    
    $version = & $pythonCmd --version 2>&1
    Write-Success " $version"
    
    # Check for virtual environment
    $venvPath = Test-VirtualEnv
    
    if ($venvPath) {
        Write-Host "  Using venv..." -NoNewline
        Enable-VirtualEnv $venvPath | Out-Null
        $pythonCmd = Join-Path $venvPath "Scripts\python.exe"
        Write-Success " $venvPath"
    } else {
        # Ask to create venv if not exists
        $createVenv = $false
        
        if (-not $Arguments -or $Arguments.Count -eq 0) {
            # Only ask in interactive mode
            Write-Warn "  No virtual environment found."
            $response = Read-Host "  Create one? (Y/n)"
            $createVenv = ($response -eq "" -or $response -match "^[Yy]")
        }
        
        if ($createVenv) {
            $venvPath = New-VirtualEnv $pythonCmd
            if ($venvPath) {
                Enable-VirtualEnv $venvPath | Out-Null
                $pythonCmd = Join-Path $venvPath "Scripts\python.exe"
            }
        }
    }
    
    # Check requirements
    Write-Host "  Checking dependencies..." -NoNewline
    
    if (-not (Test-Requirements $pythonCmd)) {
        Write-Warn " MISSING"
        
        if (-not (Install-Requirements $pythonCmd)) {
            exit 1
        }
    } else {
        Write-Success " OK"
    }
    
    Write-Host ""
    
    # Run manage.py
    $managePath = Join-Path $ScriptRoot "deployer\scripts\manage.py"
    
    if (-not (Test-Path $managePath)) {
        Write-Err "  Error: deployer\scripts\manage.py not found"
        exit 1
    }
    
    if ($Arguments -and $Arguments.Count -gt 0) {
        & $pythonCmd $managePath @Arguments
    } else {
        & $pythonCmd $managePath
    }
    
    exit $LASTEXITCODE
}

Show-Header
Main
