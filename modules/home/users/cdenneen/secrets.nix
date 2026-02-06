{
  config,
  lib,
  pkgs,
  ...
}:
{
  sops.secrets = {
    fortress_rsa.mode = "0600";
    cdenneen_ed25519_2024.mode = "0600";
    github_ed25519.mode = "0600";
    codecommit_rsa.mode = "0600";
    id_rsa_cloud9.mode = "0600";
  };

  home.activation.backupAndEnsureSshDir = lib.hm.dag.entryBefore [ "checkLinkTargets" ] ''
    mkdir -p "$HOME/.ssh"
    chmod 700 "$HOME/.ssh"

    is_hm_link() {
      local path="$1"

      if [ ! -L "$path" ]; then
        return 1
      fi

      local target
      target="$(readlink "$path" || true)"

      case "$target" in
        /nix/store/*home-manager-files/.ssh/*) return 0 ;;
      esac

      return 1
    }

    resolve_link() {
      local p="$1"
      local i=0

      while [ -L "$p" ] && [ $i -lt 20 ]; do
        local t
        t="$(readlink "$p" || true)"
        if [ -z "$t" ]; then
          break
        fi

        case "$t" in
          /*) p="$t" ;;
          *)
            p="$(cd "$(dirname "$p")" && pwd)/$t"
            ;;
        esac

        i=$((i + 1))
      done

      echo "$p"
    }

    next_backup_path() {
      local base="$1"
      local candidate="$base.save"
      local i=0

      if [ ! -e "$candidate" ]; then
        echo "$candidate"
        return 0
      fi

      while :; do
        i=$((i + 1))
        candidate="$base.save.$i"
        if [ ! -e "$candidate" ]; then
          echo "$candidate"
          return 0
        fi
      done
    }

    backup_if_unmanaged() {
      local path="$1"
      local expected_exact="''${2:-}"
      local expected_prefix="''${3:-}"

      if [ ! -e "$path" ] && [ ! -L "$path" ]; then
        return 0
      fi

      if is_hm_link "$path"; then
        return 0
      fi

      if [ -L "$path" ]; then
        local target
        target="$(readlink "$path" || true)"

        if [ -n "$expected_exact" ]; then
          local resolved
          resolved="$(resolve_link "$path")"
          if [ "$resolved" = "$expected_exact" ]; then
            return 0
          fi
        fi

        if [ -n "$expected_exact" ] && [ "$target" = "$expected_exact" ]; then
          return 0
        fi

        if [ -n "$expected_prefix" ]; then
          case "$target" in
            "$expected_prefix"*) return 0 ;;
          esac
        fi
      fi

      local backup
      backup="$(next_backup_path "$path")"
      mv "$path" "$backup"
    }

    # Private keys: symlink to decrypted SOPS secret file.
    backup_if_unmanaged "$HOME/.ssh/fortress_rsa" "${config.sops.secrets.fortress_rsa.path}"
    backup_if_unmanaged "$HOME/.ssh/github_ed25519" "${config.sops.secrets.github_ed25519.path}"
    backup_if_unmanaged "$HOME/.ssh/id_ed25519" "${config.sops.secrets.cdenneen_ed25519_2024.path}"
    backup_if_unmanaged "$HOME/.ssh/cdenneen_ed25519_2024" "${config.sops.secrets.cdenneen_ed25519_2024.path}"
    backup_if_unmanaged "$HOME/.ssh/codecommit_rsa" "${config.sops.secrets.codecommit_rsa.path}"
    backup_if_unmanaged "$HOME/.ssh/id_rsa_cloud9" "${config.sops.secrets.id_rsa_cloud9.path}"

    # Public keys: managed by Home Manager (symlink into /nix/store).
    backup_if_unmanaged "$HOME/.ssh/config" "" "/nix/store/"
    backup_if_unmanaged "$HOME/.ssh/fortress_rsa.pub" "" "/nix/store/"
    backup_if_unmanaged "$HOME/.ssh/github_ed25519.pub" "" "/nix/store/"
    backup_if_unmanaged "$HOME/.ssh/id_ed25519.pub" "" "/nix/store/"
    backup_if_unmanaged "$HOME/.ssh/cdenneen_ed25519_2024.pub" "" "/nix/store/"
    backup_if_unmanaged "$HOME/.ssh/codecommit_rsa.pub" "" "/nix/store/"
    backup_if_unmanaged "$HOME/.ssh/id_rsa_cloud9.pub" "" "/nix/store/"
  '';

  home.file = lib.mkMerge [
    {
      ".ssh/fortress_rsa".source = config.lib.file.mkOutOfStoreSymlink config.sops.secrets.fortress_rsa.path;
      ".ssh/github_ed25519".source = config.lib.file.mkOutOfStoreSymlink config.sops.secrets.github_ed25519.path;
      ".ssh/id_ed25519".source = config.lib.file.mkOutOfStoreSymlink config.sops.secrets.cdenneen_ed25519_2024.path;
      ".ssh/cdenneen_ed25519_2024".source = config.lib.file.mkOutOfStoreSymlink config.sops.secrets.cdenneen_ed25519_2024.path;
      ".ssh/codecommit_rsa".source = config.lib.file.mkOutOfStoreSymlink config.sops.secrets.codecommit_rsa.path;
      ".ssh/id_rsa_cloud9".source = config.lib.file.mkOutOfStoreSymlink config.sops.secrets.id_rsa_cloud9.path;

      ".ssh/fortress_rsa.pub".source = ../../../../pub/ssh/fortress_rsa.pub;
      ".ssh/github_ed25519.pub".source = ../../../../pub/ssh/github_ed25519.pub;
      ".ssh/id_ed25519.pub".source = ../../../../pub/ssh/id_ed25519.pub;
      ".ssh/cdenneen_ed25519_2024.pub".source = ../../../../pub/ssh/cdenneen_ed25519_2024.pub;
      ".ssh/codecommit_rsa.pub".source = ../../../../pub/ssh/codecommit_rsa.pub;
      ".ssh/id_rsa_cloud9.pub".source = ../../../../pub/ssh/id_rsa_cloud9.pub;
    }

    {
      ".local/bin/update-secrets" = {
        source = ./files/update-secrets;
        executable = true;
      };

      ".local/bin/restore-age-key" = {
        source = ./files/restore-age-key;
        executable = true;
      };
    }

    (lib.mkIf pkgs.stdenv.isDarwin {
      ".config/sops" = {
        source = config.lib.file.mkOutOfStoreSymlink "${config.home.homeDirectory}/Library/Application Support/sops";
        force = true;
      };

      ".config/sops-nix/secrets" = {
        source = config.lib.file.mkOutOfStoreSymlink "${config.home.homeDirectory}/Library/Application Support/sops-nix/secrets";
        force = true;
      };
    })
  ];
}
