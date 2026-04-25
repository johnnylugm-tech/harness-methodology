#!/bin/bash
# pre-commit hook - runs Framework Enforcement automatically
#
# Usage:
# cp .methodology/templates/pre-commit-hook.sh .git/hooks/pre-commit
# chmod +x .git/hooks/pre-commit

echo "Running Framework Enforcement..."

# Run Framework Enforcement
methodology enforce --level BLOCK
if [ $? -ne 0 ]; then
    echo "Enforcement failed. Commit blocked."
    echo "  Fix issues before committing."
    exit 1
fi

echo "Enforcement passed"
exit 0
