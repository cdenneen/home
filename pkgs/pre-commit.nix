{ writeShellScriptBin }:
writeShellScriptBin "pre-commit" ''
  echo "Running format checks..."

  # Check-only: do not modify the working tree during commit
  if command -v treefmt >/dev/null 2>&1; then
    if ! treefmt --check; then
      echo
      echo "Formatting issues detected."
      echo "Run 'nix fmt' (or 'treefmt') manually, then re-run git commit."
      exit 1
    fi
  else
    echo "treefmt not found; skipping format checks."
  fi

  exit 0
''
