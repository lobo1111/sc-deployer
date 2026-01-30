#!/usr/bin/env bash
#
# SC Deployer CLI wrapper for Linux/macOS.
# Checks Python availability, version, and dependencies before running manage.py.
#
# Usage:
#   ./cli.sh                  # Opens interactive menu
#   ./cli.sh profiles list    # Lists configured profiles
#   ./cli.sh status           # Shows overall status
#

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
GRAY='\033[0;90m'
NC='\033[0m' # No Color

info() { echo -e "${CYAN}$1${NC}"; }
success() { echo -e "${GREEN}$1${NC}"; }
warn() { echo -e "${YELLOW}$1${NC}"; }
error() { echo -e "${RED}$1${NC}"; }

# Header
show_header() {
    echo ""
    echo -e "  ${CYAN}SC Deployer${NC}"
    echo -e "  ${GRAY}==========${NC}"
    echo ""
}

# Check Python installation
check_python() {
    local python_cmds=("python3" "python")
    
    for cmd in "${python_cmds[@]}"; do
        if command -v "$cmd" &> /dev/null; then
            local version=$("$cmd" --version 2>&1)
            if [[ $version =~ Python\ ([0-9]+)\.([0-9]+)\.([0-9]+) ]]; then
                local major="${BASH_REMATCH[1]}"
                local minor="${BASH_REMATCH[2]}"
                
                if [[ $major -ge 3 && $minor -ge 10 ]]; then
                    echo "$cmd"
                    return 0
                fi
            fi
        fi
    done
    
    return 1
}

# Check virtual environment
find_venv() {
    local venv_paths=(".venv" "venv" ".env")
    
    for venv in "${venv_paths[@]}"; do
        local venv_path="$SCRIPT_DIR/$venv"
        if [[ -f "$venv_path/bin/activate" ]]; then
            echo "$venv_path"
            return 0
        fi
    done
    
    return 1
}

# Activate virtual environment
activate_venv() {
    local venv_path="$1"
    source "$venv_path/bin/activate"
}

# Check if requirements are installed
check_requirements() {
    local python_cmd="$1"
    "$python_cmd" -c "import boto3; import yaml; import questionary" 2>/dev/null
    return $?
}

# Install requirements
install_requirements() {
    local python_cmd="$1"
    local req_file="$SCRIPT_DIR/deployer/requirements.txt"
    
    info "  Installing dependencies..."
    "$python_cmd" -m pip install -q -r "$req_file"
    
    if [[ $? -ne 0 ]]; then
        error "  Failed to install dependencies"
        return 1
    fi
    
    success "  Dependencies installed"
    return 0
}

# Create virtual environment
create_venv() {
    local python_cmd="$1"
    local venv_path="$SCRIPT_DIR/.venv"
    
    info "  Creating virtual environment..."
    "$python_cmd" -m venv "$venv_path"
    
    if [[ $? -ne 0 ]]; then
        error "  Failed to create virtual environment"
        return 1
    fi
    
    success "  Virtual environment created at .venv"
    echo "$venv_path"
    return 0
}

# Main
main() {
    # Check Python
    echo -n "  Checking Python..."
    
    python_cmd=$(check_python)
    
    if [[ -z "$python_cmd" ]]; then
        error " NOT FOUND"
        echo ""
        error "  Python 3.10+ is required but not found."
        echo ""
        echo "  Install Python:"
        echo "    macOS:  brew install python@3.12"
        echo "    Ubuntu: sudo apt install python3.12"
        echo "    Fedora: sudo dnf install python3.12"
        echo ""
        exit 1
    fi
    
    version=$("$python_cmd" --version 2>&1)
    success " $version"
    
    # Check for virtual environment
    venv_path=$(find_venv) || venv_path=""
    
    if [[ -n "$venv_path" ]]; then
        echo -n "  Using venv..."
        activate_venv "$venv_path"
        python_cmd="$venv_path/bin/python"
        success " $venv_path"
    else
        # Ask to create venv if not exists (only in interactive mode)
        if [[ $# -eq 0 ]]; then
            warn "  No virtual environment found."
            read -p "  Create one? (Y/n) " -n 1 -r
            echo
            
            if [[ $REPLY =~ ^[Yy]?$ ]]; then
                venv_path=$(create_venv "$python_cmd")
                if [[ -n "$venv_path" ]]; then
                    activate_venv "$venv_path"
                    python_cmd="$venv_path/bin/python"
                fi
            fi
        fi
    fi
    
    # Check requirements
    echo -n "  Checking dependencies..."
    
    if ! check_requirements "$python_cmd"; then
        warn " MISSING"
        
        if ! install_requirements "$python_cmd"; then
            exit 1
        fi
    else
        success " OK"
    fi
    
    echo ""
    
    # Run manage.py
    manage_path="$SCRIPT_DIR/deployer/scripts/manage.py"
    
    if [[ ! -f "$manage_path" ]]; then
        error "  Error: deployer/scripts/manage.py not found"
        exit 1
    fi
    
    local exit_code=0
    "$python_cmd" "$manage_path" "$@" || exit_code=$?
    
    # Deactivate venv on exit
    if [[ -n "$venv_path" ]] && command -v deactivate &> /dev/null; then
        deactivate 2>/dev/null || true
    fi
    
    exit $exit_code
}

show_header
main "$@"
