{ config, lib, ... }:
let
  cfg = config.programs.git;
in
{
  config = lib.mkIf cfg.enable {
    catppuccin = {
      delta = {
        enable = true;
        flavor = config.catppuccin.flavor;
      };
      lazygit = {
        enable = true;
        flavor = config.catppuccin.flavor;
        accent = config.catppuccin.accent;
      };
    };
    programs = {
      delta = {
        enable = true;
        enableGitIntegration = true;
      };
      git = {
        lfs.enable = true;
        settings = {
          pull.rebase = "true";
          rebase.autostash = "true";
          core.editor = "nvim";
          core.eol = "lf";
          core.autocrlf = "input";
          # Default to SSH signing; users/hosts can override if needed.
          gpg.format = lib.mkDefault "ssh";
          init.defaultBranch = "main";
          url."git@github.com:".pushInsteadOf = "https://github.com/";
          alias = {
            a = "add";
            aa = "add -A";
            b = "branch";
            ba = "branch -a";
            c = "commit -m";
            ca = "commit -am";
            cam = "commit --amend --date=now";
            co = ''
              !f(){
                            use_synth="$(git config --bool --get opencode.syntheticWorktrees 2>/dev/null || true)";
                            if [ "$use_synth" != "true" ]; then
                              exec git checkout "$@";
                            fi

                            if [ "$1" = "-b" ] || [ "$1" = "-B" ]; then
                              shift
                              if [ -z "$1" ]; then
                                echo "usage: git co -b <branch> [start-point]" >&2
                                exit 2
                              fi
                              start="$2"
                              [ -z "$start" ] && start="origin/main"
                              exec git ws-branch "$1" "$start"
                            fi

                            if [ $# -ne 1 ]; then
                              exec git checkout "$@";
                            fi

                            b="$1";
                            case "$b" in
                              -*|*:*|*/*) exec git checkout "$@" ;;
                              *@*) exec git checkout "$b" ;;
                            esac

                            ws="$WORKSPACE_NAME";
                            if [ -z "$ws" ]; then
                              ws="$(basename "$PWD")";
                            fi
                            ws="$(printf '%s' "$ws" | tr -cs 'A-Za-z0-9._-' '-' | sed 's/^-//; s/-$//')";
                            [ -n "$ws" ] || { echo "invalid workspace name" >&2; exit 2; };

                            syn="$b@$ws";
                            if git show-ref --verify --quiet "refs/heads/$syn"; then
                              exec git switch "$syn";
                            fi

                            if git show-ref --verify --quiet "refs/remotes/origin/$b"; then
                              git branch -f "$syn" "origin/$b" >/dev/null 2>&1 || exit $?;
                              git branch --set-upstream-to "origin/$b" "$syn" >/dev/null 2>&1 || true;
                              exec git switch "$syn";
                            fi

                            echo "branch '$b' not found; use: git ws-branch $b" >&2;
                            exit 2;
                          }; f'';
            cob = "ws-branch";

            sw = ''
              !f(){
                            use_synth="$(git config --bool --get opencode.syntheticWorktrees 2>/dev/null || true)";
                            if [ "$use_synth" != "true" ]; then
                              exec git switch "$@";
                            fi

                            if [ "$1" = "-c" ] || [ "$1" = "-C" ]; then
                              shift
                              if [ -z "$1" ]; then
                                echo "usage: git sw -c <branch> [start-point]" >&2
                                exit 2
                              fi
                              start="$2"
                              [ -z "$start" ] && start="origin/main"
                              exec git ws-branch "$1" "$start";
                            fi

                            if [ $# -ne 1 ]; then
                              exec git switch "$@";
                            fi

                            b="$1";
                            case "$b" in
                              *@*) exec git switch "$b" ;;
                            esac

                            ws="$WORKSPACE_NAME";
                            if [ -z "$ws" ]; then
                              ws="$(basename "$PWD")";
                            fi
                            ws="$(printf '%s' "$ws" | tr -cs 'A-Za-z0-9._-' '-' | sed 's/^-//; s/-$//')";
                            [ -n "$ws" ] || { echo "invalid workspace name" >&2; exit 2; };
                            syn="$b@$ws";

                            if git show-ref --verify --quiet "refs/heads/$syn"; then
                              exec git switch "$syn";
                            fi

                            if git show-ref --verify --quiet "refs/remotes/origin/$b"; then
                              git branch -f "$syn" "origin/$b" >/dev/null 2>&1 || exit $?;
                              git branch --set-upstream-to "origin/$b" "$syn" >/dev/null 2>&1 || true;
                              exec git switch "$syn";
                            fi

                            echo "branch '$b' not found; use: git ws-branch $b" >&2;
                            exit 2;
                          }; f'';

            swc = "ws-branch";
            s = "status -sb";
            # With synthetic branches, rely on upstream tracking.
            po = "push";
            d = "diff";
            dc = "diff --cached";
            ignore = "update-index --assume-unchanged";
            unignore = "update-index --no-assume-unchanged";
            ignored = "!git ls-files -v | grep ^h | cut -c 3-";
            rbm = "!git fetch && git rebase origin/main";
            rbc = "-c core.editor=true rebase --continue";

            ws-branch = ''
              !f(){
                            base="$1";
                            start="$2";
                            if [ -z "$base" ]; then
                              echo "usage: git ws-branch <branch> [start-point]" >&2;
                              exit 2;
                            fi
                            if [ -z "$start" ]; then
                              start="origin/main";
                            fi

                            ws="$WORKSPACE_NAME";
                            if [ -z "$ws" ]; then
                              ws="$(basename "$PWD")";
                            fi
                            ws="$(printf '%s' "$ws" | tr -cs 'A-Za-z0-9._-' '-' | sed 's/^-//; s/-$//')";
                            if [ -z "$ws" ]; then
                              echo "invalid workspace name" >&2;
                              exit 2;
                            fi

                            localb="$base@$ws";
                            remoteb="$base";

                            git fetch -p origin >/dev/null 2>&1 || true;
                            git switch -c "$localb" "$start" || exit $?;
                            git config push.default upstream;
                            git config opencode.syntheticWorktrees true;
                            git push -u origin "$localb:$remoteb";
                          }; f'';
            ws-setup = "!setup_repo";
            ws-update = "!update_workspace";
            clone = ''
              !f(){
                            if [ $# -lt 1 ]; then
                              echo "usage: git clone <url> [path]" >&2
                              exit 2
                            fi

                            url="\$1"
                            dest="''${2:-}"
                            shift || true

                            case "\$url" in
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
                                echo "git clone: unsupported URL: \$url" >&2
                                exit 2
                                ;;
                            esac

                            path="''${path%.git}"
                            repo="''${path##*/}"

                            if [ -z "\$dest" ]; then
                              target_dir="."
                              target_name="\$repo"
                            else
                              target_dir="''${dest%/*}"
                              [ "\$target_dir" = "\$dest" ] && target_dir="."
                              target_name="''${dest##*/}"
                            fi

                            if [ -e "\$target_dir/\$target_name" ]; then
                              echo "git clone: destination exists: \$target_dir/\$target_name" >&2
                              exit 1
                            fi

                            cache_root="''${CACHE_ROOT:-\$HOME/src/cache}"
                            cache_repo="''${cache_root}/''${host}_''${path//\//_}.git"

                            mkdir -p "\$cache_root"
                            if [ ! -d "\$cache_repo" ]; then
                              git clone --bare "\$url" "\$cache_repo"
                            fi
                            git --git-dir="\$cache_repo" config --add remote.origin.fetch "+refs/heads/*:refs/remotes/origin/*" >/dev/null 2>&1 || true
                            git --git-dir="\$cache_repo" fetch --prune origin

                            branch="\$(git --git-dir="\$cache_repo" symbolic-ref -q refs/remotes/origin/HEAD | sed 's@^refs/remotes/origin/@@')"
                            if [ -z "\$branch" ]; then
                              if git --git-dir="\$cache_repo" show-ref --verify --quiet refs/remotes/origin/main; then
                                branch="main"
                              elif git --git-dir="\$cache_repo" show-ref --verify --quiet refs/remotes/origin/master; then
                                branch="master"
                              else
                                echo "git clone: unable to determine default branch" >&2
                                exit 1
                              fi
                            fi

                            ws="\$WORKSPACE_NAME"
                            if [ -z "\$ws" ]; then
                              ws="\$(basename "\$(cd "\$target_dir" && pwd)")"
                            fi
                            ws="\$(printf '%s' "\$ws" | tr -cs 'A-Za-z0-9._-' '-' | sed 's/^-//; s/-$//')"
                            [ -n "\$ws" ] || { echo "invalid workspace name" >&2; exit 2; }

                            localb="\$branch@\$ws"
                            git --git-dir="\$cache_repo" worktree add -B "\$localb" "\$target_dir/\$target_name" "origin/\$branch"
                            git -C "\$target_dir/\$target_name" branch --set-upstream-to="origin/\$branch" "\$localb" >/dev/null 2>&1 || true
                            echo "Added worktree \$target_dir/\$target_name on \$localb (tracks origin/\$branch)"
                          }; f'';
          };
        };
      };
      lazygit.enable = true;
    };
  };
}
