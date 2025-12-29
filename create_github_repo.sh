#!/bin/bash
# Script to create GitHub repository and push code

set -e

echo "Creating GitHub repository 'TC_datalogger'..."

# Create the repository and push
gh repo create TC_datalogger --public --source=. --remote=origin --push

echo ""
echo "✅ Repository created successfully!"
echo "🔗 View it at: https://github.com/$(gh api user --jq .login)/TC_datalogger"

