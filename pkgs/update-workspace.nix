{ pkgs }:
pkgs.writeShellScriptBin "update_workspace" ''
  set -euo pipefail

  migrate=0
  root="''${WORKSPACE_ROOT:-$HOME/src/workspace}"

  while [ $# -gt 0 ]; do
    case "$1" in
      --migrate) migrate=1; shift ;;
      --root) root="$2"; shift 2 ;;
      *)
        echo "usage: update_workspace [--migrate] [--root <dir>]" >&2
        exit 2
        ;;
    esac
  done

  if [ ! -d "$root" ]; then
    echo "update_workspace: root not found: $root" >&2
    exit 1
  fi

  cache_root="''${CACHE_ROOT:-$HOME/src/cache}"

  for repo in "$root"/*; do
    [ -d "$repo" ] || continue
    [ -f "$repo/.git" ] || continue

    gitdir_line="$(${pkgs.coreutils}/bin/cat "$repo/.git")"
    gitdir_path="''${gitdir_line#gitdir: }"
    gitdir_path="$(${pkgs.coreutils}/bin/realpath -m "$repo/''${gitdir_path}")"

    origin_url="$(${pkgs.git}/bin/git -C "$repo" remote get-url origin 2>/dev/null || true)"
    if [ -z "$origin_url" ]; then
      echo "skip: $repo (no origin)"
      continue
    fi

    case "$origin_url" in
      git@*:* )
        host="''${origin_url#git@}"
        host="''${host%%:*}"
        path="''${origin_url#git@''${host}:}"
        ;;
      http://*/*|https://*/* )
        host="''${origin_url#*://}"
        host="''${host%%/*}"
        path="''${origin_url#*://''${host}/}"
        ;;
      ssh://git@*/* )
        host="''${origin_url#ssh://git@}"
        host="''${host%%/*}"
        path="''${origin_url#ssh://git@''${host}/}"
        ;;
      * )
        echo "skip: $repo (unsupported origin URL)" >&2
        continue
        ;;
    esac

    path="''${path%.git}"
    expected="''${cache_root}/''${host}_''${path//\//_}.git"

    if [ "$gitdir_path" = "$expected" ]; then
      echo "ok: $repo"
      continue
    fi

    echo "mismatch: $repo"
    echo "  current: $gitdir_path"
    echo "  expected: $expected"

    if [ $migrate -eq 1 ]; then
      ts="$(${pkgs.coreutils}/bin/date +%Y%m%d%H%M%S)"
      backup="''${repo}.bak.''${ts}"
      echo "  migrating -> $backup"
      ${pkgs.coreutils}/bin/mv "$repo" "$backup"

      branch="$(${pkgs.git}/bin/git -C "$backup" rev-parse --abbrev-ref HEAD 2>/dev/null || true)"
      if [ -z "$branch" ] || [ "$branch" = "HEAD" ]; then
        branch=""
      fi

      (cd "$root" && setup_repo "$origin_url" "$branch")
      echo "  migrated: $repo (backup at $backup)"
    fi
  done
''
