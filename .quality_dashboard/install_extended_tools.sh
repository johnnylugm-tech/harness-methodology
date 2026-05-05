#!/bin/bash

# Install Extended Dimensions Tools
# Usage: ./scripts/install_extended_tools.sh [--high|--medium|--low|--all]

set -e

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

log_info() {
  echo -e "${GREEN}ℹ${NC} $1"
}

log_warn() {
  echo -e "${YELLOW}⚠${NC} $1"
}

log_error() {
  echo -e "${RED}✗${NC} $1"
}

log_success() {
  echo -e "${GREEN}✓${NC} $1"
}

check_tool() {
  local tool=$1
  local manager=$2

  case $manager in
    pip3)
      if pip3 show "$tool" &>/dev/null; then
        local version=$(pip3 show "$tool" 2>/dev/null | grep "^Version:" | awk '{print $2}')
        log_success "$tool v$version"
        return 0
      fi
      ;;
    npm)
      if npm list -g "$tool" &>/dev/null 2>&1; then
        log_success "$tool installed"
        return 0
      fi
      ;;
    brew)
      if brew list "$tool" &>/dev/null 2>&1; then
        local version=$(brew list --versions "$tool" 2>/dev/null | awk '{print $NF}')
        log_success "$tool v$version"
        return 0
      fi
      ;;
  esac
  return 1
}

install_high_priority() {
  echo
  log_info "=== Mutation Testing (HIGH Priority) ==="

  log_info "Installing mutmut..."
  pip3 install mutmut || log_error "Failed to install mutmut"

  log_info "Installing stryker..."
  npm install -g stryker stryker-cli || log_error "Failed to install stryker"

  log_info "Verifying..."
  check_tool "mutmut" "pip3" || log_error "mutmut verification failed"
  check_tool "stryker" "npm" || log_error "stryker verification failed"
}

install_medium_priority() {
  echo
  log_info "=== Property Testing, Fuzzing, Accessibility (MEDIUM Priority) ==="

  log_info "Installing hypothesis..."
  pip3 install hypothesis || log_error "Failed to install hypothesis"

  log_info "Installing fast-check..."
  npm install -g fast-check || log_error "Failed to install fast-check"

  log_info "Installing atheris..."
  pip3 install atheris || log_warn "atheris requires Python 3.9+ (optional)"

  log_info "Installing accessibility tools (pa11y, axe-core)..."
  npm install -g pa11y axe-core || log_error "Failed to install accessibility tools"

  log_info "Verifying..."
  check_tool "hypothesis" "pip3" || log_error "hypothesis verification failed"
  check_tool "fast-check" "npm" || log_error "fast-check verification failed"
  check_tool "pa11y" "npm" || log_error "pa11y verification failed"
}

install_low_priority() {
  echo
  log_info "=== License, Observability, Supply Chain (LOW Priority) ==="

  log_info "Installing scancode..."
  pip3 install scancode || log_error "Failed to install scancode"

  log_info "Installing fossa..."
  npm install -g fossa || log_error "Failed to install fossa"

  log_info "Installing observability tools (syft, grype)..."
  brew install syft grype || log_warn "brew tools require macOS"

  log_info "Installing cosign..."
  brew install sigstore/tap/cosign || log_warn "cosign requires homebrew (macOS)"

  log_info "Verifying..."
  check_tool "scancode" "pip3" || log_error "scancode verification failed"
  check_tool "fossa" "npm" || log_error "fossa verification failed"
}

verify_all() {
  echo
  log_info "=== Verification Report ==="
  echo

  echo "Python (pip3):"
  for tool in mutmut hypothesis atheris scancode; do
    if check_tool "$tool" "pip3"; then
      :
    else
      log_error "$tool not installed"
    fi
  done

  echo
  echo "JavaScript (npm):"
  for tool in stryker fast-check pa11y axe-core fossa; do
    if check_tool "$tool" "npm"; then
      :
    else
      log_error "$tool not installed"
    fi
  done

  echo
  echo "macOS (brew):"
  for tool in syft grype cosign; do
    if check_tool "$tool" "brew"; then
      :
    else
      log_warn "$tool not installed (optional, macOS only)"
    fi
  done
}

# Parse arguments
PRIORITY="all"
if [[ $# -gt 0 ]]; then
  PRIORITY="$1"
fi

echo "╔════════════════════════════════════════════════════════════╗"
echo "║     Extended Dimensions Tools Installation Script         ║"
echo "╚════════════════════════════════════════════════════════════╝"

case $PRIORITY in
  --high)
    install_high_priority
    ;;
  --medium)
    install_medium_priority
    ;;
  --low)
    install_low_priority
    ;;
  --all)
    install_high_priority
    install_medium_priority
    install_low_priority
    ;;
  *)
    echo "Usage: $0 [--high|--medium|--low|--all]"
    echo "  --high:   Install mutation testing tools only"
    echo "  --medium: Install property testing, fuzzing, accessibility"
    echo "  --low:    Install compliance, observability, supply chain"
    echo "  --all:    Install all extended dimension tools (default)"
    exit 1
    ;;
esac

verify_all

echo
log_success "Installation complete!"
echo
log_info "Next: Enable extended dimensions in config.advanced.yaml"
echo "       Set 'enabled: true' for each dimension you installed tools for"
