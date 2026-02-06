{ writeShellScriptBin }:
writeShellScriptBin "pre-commit" ''
  echo "Running format checks..."

  # Check-only: do not modify the working tree during commit
  if command -v treefmt >/dev/null 2>&1; then
    treefmt_cmd="treefmt --fail-on-change"
  else
    treefmt_cmd="nix fmt -- --check"
  fi

  if ! sh -c "$treefmt_cmd"; then
    echo
    echo "Formatting issues detected."
    echo "Run 'nix fmt' to fix, then re-run git commit."
    exit 1
  fi

  exit 0
''
