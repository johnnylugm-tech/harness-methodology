#!/bin/bash
# Create GitHub Release
# ======================
# Automates GitHub Release creation using gh CLI
#
# Usage:
#   ./scripts/create_release.sh          # Create release from latest tag
#   ./scripts/create_release.sh 6.12.0   # Create release for specific version

set -e

cd "$(dirname "$0")/.."

VERSION="${1:-$(git describe --tags --abbrev=0 2>/dev/null || echo "")}"

if [ -z "$VERSION" ]; then
    echo "No version specified and no git tag found."
    echo "   Usage: $0 [VERSION]"
    exit 1
fi

VERSION="${VERSION#v}"

if ! command -v gh &> /dev/null; then
    echo "GitHub CLI (gh) is not installed."
    echo "   Install: https://cli.github.com/"
    exit 1
fi

CHANGELOG_FILE="CHANGELOG.md"
if [ -f "$CHANGELOG_FILE" ]; then
    CHANGELOG=$(awk -v ver="## v$VERSION" '
        $0 ~ ver {found=1; print; next}
        found && /^## v[0-9]/ {exit}
        found {print}
    ' "$CHANGELOG_FILE" | head -50)
    [ -z "$CHANGELOG" ] && CHANGELOG="See CHANGELOG.md for details."
else
    CHANGELOG="See repository for details."
fi

echo "Creating GitHub Release v$VERSION..."

gh release create "v$VERSION" \
    --title "v$VERSION" \
    --notes "$CHANGELOG" \
    --target main

echo "Release v$VERSION created successfully."
