{ writeShellScriptBin }:
writeShellScriptBin "pre-commit" ''
  set -euo pipefail

  echo "Stashing unstaged changes..."
  stash_output=$(git stash push --keep-index --include-untracked --message 'Unstaged changes')
  echo "$stash_output"

  echo "Formatting..."
  nix fmt

  git add --all

  if [ -n "$stash_output" ] && [ "$stash_output" != "No local changes to save" ]; then
    echo "Restoring unstaged changes..."
    git stash pop
  else
    echo "No unstaged changes to restore."
  fi
''
