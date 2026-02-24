{ pkgs }:
pkgs.writeShellScriptBin "setup_repo" ''
  set -euo pipefail

  if [ $# -lt 1 ] || [ $# -gt 2 ]; then
    echo "usage: setup_repo <git-url> [branch]" >&2
    exit 2
  fi

  url="$1"
  branch_arg="${"2:-"}"

  case "$url" in
    git@*:* )
      host="''${url#git@}"
      host="''${host%%:*}"
      path="''${url#git@''${host}:}"
      ;;
    http://*/*|https://*/* )
      host="''${url#*://}"
      host="''${host%%/*}"
      path="''${url#*://''${host}/}"
      ;;
    ssh://git@*/* )
      host="''${url#ssh://git@}"
      host="''${host%%/*}"
      path="''${url#ssh://git@''${host}/}"
      ;;
    * )
      echo "setup_repo: unsupported URL: $url" >&2
      exit 2
      ;;
  esac

  path="''${path%.git}"
  repo="''${path##*/}"

  cache_root="''${CACHE_ROOT:-$HOME/src/cache}"
  cache_repo="''${cache_root}/''${host}_''${path//\//_}.git"

  ${pkgs.coreutils}/bin/mkdir -p "$cache_root"

  if [ ! -d "$cache_repo" ]; then
    ${pkgs.git}/bin/git clone --bare "$url" "$cache_repo"
  fi

  ${pkgs.git}/bin/git --git-dir="$cache_repo" config --add remote.origin.fetch "+refs/heads/*:refs/remotes/origin/*" >/dev/null 2>&1 || true
  ${pkgs.git}/bin/git --git-dir="$cache_repo" fetch --prune origin

  if [ -n "$branch_arg" ]; then
    branch="$branch_arg"
  else
    branch="$(${pkgs.git}/bin/git --git-dir="$cache_repo" symbolic-ref -q refs/remotes/origin/HEAD | ${pkgs.coreutils}/bin/sed 's@^refs/remotes/origin/@@')"
    if [ -z "$branch" ]; then
      if ${pkgs.git}/bin/git --git-dir="$cache_repo" show-ref --verify --quiet refs/remotes/origin/main; then
        branch="main"
      elif ${pkgs.git}/bin/git --git-dir="$cache_repo" show-ref --verify --quiet refs/remotes/origin/master; then
        branch="master"
      else
        echo "setup_repo: unable to determine default branch" >&2
        exit 1
      fi
    fi
  fi

  workspace="''${PWD##*/}"
  local_branch="''${branch}@''${workspace}"

  if [ -e "$repo" ]; then
    echo "setup_repo: destination exists: $repo" >&2
    exit 1
  fi

  ${pkgs.git}/bin/git --git-dir="$cache_repo" worktree add -B "$local_branch" "$repo" "origin/$branch"
  ${pkgs.git}/bin/git -C "$repo" branch --set-upstream-to="origin/$branch" "$local_branch" >/dev/null 2>&1 || true
  echo "Added worktree $repo on $local_branch (tracks origin/$branch)"
''
