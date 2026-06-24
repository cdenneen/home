{
  config,
  lib,
  pkgs,
  osConfig ? null,
  nixHostName ? null,
  ...
}:
let
  hostName =
    if osConfig != null then
      (osConfig.networking.hostName or "")
    else if nixHostName != null then
      nixHostName
    else
      builtins.getEnv "HOSTNAME";
  isNyx = hostName == "nyx";
  isDarwin = pkgs.stdenv.isDarwin;
  isLinux = pkgs.stdenv.isLinux;
  # Avoid runtime-dir secret paths on Linux (e.g. /run/user/$UID) which may be
  # missing when the user session is not active (notably on headless hosts).
  linuxSopsSecretsDir = "${config.home.homeDirectory}/.local/share/sops-nix/secrets";
  darwinSopsSecretsDir = "${config.home.homeDirectory}/.config/sops-nix/secrets";
  sopsSecretsDir = if isDarwin then darwinSopsSecretsDir else linuxSopsSecretsDir;
  useNyxRemoteMcp = isDarwin && !isNyx;
  useSharedNyxMcp = useNyxRemoteMcp || isNyx;
  nyxSharedMcpHost = if isNyx then "127.0.0.1" else "nyx.tail0e55.ts.net";
  nyxSharedMcpUrl = port: "http://${nyxSharedMcpHost}:${toString port}/mcp";

  # When running on nyx itself, prefer localhost to avoid any tailscale/DNS weirdness.
  recalliumMcpUrl = nyxSharedMcpUrl 18001;

  mkSharedOpencodeMcp = port: {
    type = "remote";
    url = nyxSharedMcpUrl port;
    enabled = true;
    timeout = 60000;
  };

  mkOpencodeMcpCommand = script: [
    "bash"
    "-lc"
    script
  ];

  mkOpencodeMcp =
    port: script:
    if useSharedNyxMcp then
      mkSharedOpencodeMcp port
    else
      {
        type = "local";
        command = mkOpencodeMcpCommand script;
        enabled = true;
      };

  mkNyxOnlyOpencodeMcp =
    port: script:
    if isNyx then
      mkSharedOpencodeMcp port
    else
      {
        type = "local";
        command = mkOpencodeMcpCommand script;
        enabled = true;
      };

  mkLocalOpencodeMcpCommand = script: [
    "bash"
    "-lc"
    script
  ];

  mcpGitlabScript = ''
    set -euo pipefail

    export GITLAB_API_URL="https://git.ap.org/api/v4"
    export GITLAB_READ_ONLY_MODE="true"

    if [ -z "''${GITLAB_PERSONAL_ACCESS_TOKEN:-}" ] && command -v glab >/dev/null 2>&1; then
      token="$(glab auth token -h git.ap.org 2>/dev/null || true)"
      if [ -z "$token" ]; then
        token="$(glab auth token 2>/dev/null || true)"
      fi
      if [ -n "$token" ]; then
        export GITLAB_PERSONAL_ACCESS_TOKEN="$token"
      fi
    fi

    exec npx -y @zereight/mcp-gitlab
  '';

  mcpKubernetesScript = ''
    set -euo pipefail

    kubeconfig="''${KUBECONFIG:-$HOME/.kube/config}"
    if [ -r "$kubeconfig" ]; then
      sanitized="''${TMPDIR:-/tmp}/codex-kubeconfig.$$"
      sed -E 's/^([[:space:]]*-[[:space:]]+)no([[:space:]]*)$/\1"no"\2/' "$kubeconfig" > "$sanitized"
      export KUBECONFIG="$sanitized"
    fi

    exec npx -y @strowk/mcp-k8s
  '';

  mcpAwsScript = ''
    set -euo pipefail
    export LOG_LEVEL="error"
    exec npx -y aws-mcp-readonly-lite
  '';

  mcpTerraformScript = ''
    set -euo pipefail

    if command -v podman >/dev/null 2>&1 && podman info >/dev/null 2>&1; then
      exec podman run -i --rm hashicorp/terraform-mcp-server:0.4.0
    fi

    exec npx -y terraform-mcp-server
  '';

  mcpDuckDuckGoScript = ''
    set -euo pipefail
    exec npx -y ddg-mcp-search
  '';

  mcpContext7Script = ''
    set -euo pipefail
    exec npx -y @upstash/context7-mcp
  '';

  mcpPlaywrightScript = ''
    set -euo pipefail
    exec npx -y @playwright/mcp
  '';

  opencodeConfigJson = builtins.toJSON {
    "$schema" = "https://opencode.ai/config.json";
    # Keep compaction enabled so long sessions stay responsive.
    compaction = {
      auto = true;
      prune = true;
      reserved = 10000;
    };
    mcp = {
      gitlab = mkOpencodeMcp 18101 mcpGitlabScript;
      recallium = {
        type = "remote";
        url = recalliumMcpUrl;
        enabled = true;
        timeout = 60000;
      };
      supabase = {
        type = "remote";
        url = "https://mcp.supabase.com/mcp?project_ref=kefpmmjhtdxhhhcndrnx";
        enabled = true;
        timeout = 60000;
      };
      kubernetes = mkOpencodeMcp 18102 mcpKubernetesScript;
      aws = mkOpencodeMcp 18103 mcpAwsScript;
      terraform = mkOpencodeMcp 18104 mcpTerraformScript;
      duckduckgo = mkOpencodeMcp 18105 mcpDuckDuckGoScript;
      context7 =
        (mkOpencodeMcp 18106 mcpContext7Script)
        // lib.optionalAttrs (!useSharedNyxMcp) {
          environment = {
            CONTEXT7_API_KEY = "{env:CONTEXT7_API_KEY}";
          };
        };
      playwright = (mkNyxOnlyOpencodeMcp 18107 mcpPlaywrightScript) // {
        enabled = true;
        timeout = 120000;
      };
    };
    permission = {
      skill = {
        "*" = "allow";
      };
    };
    experimental = {
      mcp_timeout = 60000;
    };
    skills = {
      paths = [
        "/home/cdenneen/.agents/skills"
        "/home/cdenneen/.opencode/skills"
      ];
    };
  };
in
{
  programs.onepassword-secrets = {
    enable = true;
    tokenFile = "${config.home.homeDirectory}/.config/opnix/token";
    secrets = {
      gitlabToken = {
        reference = "op://keys/gitlab/credential";
        path = ".config/opnix/gitlab_token";
        mode = "0600";
      };
      chatOauthClientSecret = {
        reference = "op://keys/chat_oauth2/credential";
        path = ".config/opnix/chat_oauth_client_secret";
        mode = "0600";
      };
      chatOauthCookieSecret = {
        reference = "op://keys/chat_oauth2/cookie_secret";
        path = ".config/opnix/chat_oauth_cookie_secret";
        mode = "0600";
      };
    };
  };

  sops.secrets = {
    fortress_rsa = {
      mode = "0600";
      path = "${sopsSecretsDir}/fortress_rsa";
    };
    cdenneen_ed25519_2024 = {
      mode = "0600";
      path = "${sopsSecretsDir}/cdenneen_ed25519_2024";
    };
    github_ed25519 = {
      mode = "0600";
      path = "${sopsSecretsDir}/github_ed25519";
    };
    codecommit_rsa = {
      mode = "0600";
      path = "${sopsSecretsDir}/codecommit_rsa";
    };
    id_rsa_cloud9 = {
      mode = "0600";
      path = "${sopsSecretsDir}/id_rsa_cloud9";
    };

    openai_api_key.mode = "0400";
    github-token = {
      mode = "0400";
      path = "${sopsSecretsDir}/github-token";
    };
    gemini_api_key.mode = "0400";
    supabase_access_token.mode = "0400";
    cloudflare_account_api_token.mode = "0400";
    telegram_bot_token.mode = "0400";
    telegram_chat_id.mode = "0400";

    glab_cli_config = {
      mode = "0600";
      path = "${config.home.homeDirectory}/.config/glab-cli/config.yml";
    };

    oci_config.mode = "0600";

    oci_private_key.mode = "0600";
  }
  // lib.optionalAttrs isNyx {
    opencode_telegram_notify_ts.mode = "0600";
  };

  home.activation.backupAndEnsureSshDir = lib.hm.dag.entryBefore [ "checkLinkTargets" ] ''
    mkdir -p "$HOME/.ssh"
    chmod 700 "$HOME/.ssh"
    mkdir -p "$HOME/.oci"
    chmod 700 "$HOME/.oci"

    mkdir -p "$HOME/.local/share/sops-nix/secrets"
    chmod 700 "$HOME/.local/share/sops-nix"
    chmod 700 "$HOME/.local/share/sops-nix/secrets"

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
    backup_if_unmanaged "$HOME/.config/gh/hosts.yml"
  '';

  home.activation.fixDarwinActivationPath = lib.mkIf pkgs.stdenv.isDarwin (
    lib.hm.dag.entryAfter [ "writeBoundary" ] ''
      export PATH="${pkgs.coreutils}/bin:${pkgs.gettext}/bin:/usr/bin:/bin:/usr/sbin:/sbin:''${PATH:-}"
    ''
  );
  home.activation.fixDarwinSopsSecretsDir = lib.mkIf pkgs.stdenv.isDarwin (
    lib.hm.dag.entryBefore [ "sops-nix" ] ''
      set -euo pipefail

      secrets_dir="$HOME/.config/sops-nix/secrets"

      if [ -L "$secrets_dir" ] && [ ! -e "$secrets_dir" ]; then
        $DRY_RUN_CMD rm -f "$secrets_dir"
      fi

      $DRY_RUN_CMD mkdir -p "$HOME/.config/sops-nix"
      $DRY_RUN_CMD chmod 700 "$HOME/.config/sops-nix"
    ''
  );

  home.activation.materializeDarwinSopsSecrets = lib.mkIf pkgs.stdenv.isDarwin (
    lib.hm.dag.entryAfter [ "sops-nix" ] ''
      set -euo pipefail

      export PATH="${pkgs.coreutils}/bin:${pkgs.gettext}/bin:/usr/bin:/bin:/usr/sbin:/sbin:''${PATH:-}"

      sops_nix_program="${config.launchd.agents.sops-nix.config.Program}"

      if [ -x "$sops_nix_program" ]; then
        $DRY_RUN_CMD "$sops_nix_program"
      elif command -v sops-nix-user >/dev/null 2>&1; then
        $DRY_RUN_CMD sops-nix-user
      fi
    ''
  );

  # On headless Linux hosts, `nh home switch` is sometimes executed via `su -` from
  # root which does not create a user session (so `/run/user/$UID` and
  # $XDG_RUNTIME_DIR are missing). sops-nix relies on XDG_RUNTIME_DIR to mount
  # secrets, so provide a fallback under $HOME and materialize once during
  # activation.
  home.activation.materializeLinuxSopsSecrets = lib.mkIf pkgs.stdenv.isLinux (
    lib.hm.dag.entryAfter [ "sops-nix" ] ''
      set -euo pipefail

      export PATH="${pkgs.coreutils}/bin:${pkgs.gettext}/bin:/usr/bin:/bin:/usr/sbin:/sbin:''${PATH:-}"

      if [ -z "''${XDG_RUNTIME_DIR:-}" ] || [ ! -d "''${XDG_RUNTIME_DIR:-}" ]; then
        export XDG_RUNTIME_DIR="$HOME/.local/share/xdg-runtime"
      fi

      $DRY_RUN_CMD mkdir -p "$XDG_RUNTIME_DIR"
      $DRY_RUN_CMD chmod 700 "$XDG_RUNTIME_DIR"

      if command -v sops-nix-user >/dev/null 2>&1; then
        $DRY_RUN_CMD sops-nix-user
      fi
    ''
  );

  home.activation.materializeOciFiles =
    lib.hm.dag.entryAfter
      (if pkgs.stdenv.isDarwin then [ "materializeDarwinSopsSecrets" ] else [ "sops-nix" ])
      ''
        set -euo pipefail

        src_cfg="$HOME/.config/sops-nix/secrets/oci_config"
        src_key="$HOME/.config/sops-nix/secrets/oci_private_key"
        dst_dir="$HOME/.oci"

        if [ ! -r "$src_cfg" ] || [ ! -r "$src_key" ]; then
          echo "warning: OCI secrets are missing from sops-nix output; skipping OCI materialization ($src_cfg, $src_key)" >&2
          exit 0
        fi

        $DRY_RUN_CMD mkdir -p "$dst_dir"
        $DRY_RUN_CMD chmod 700 "$dst_dir"

        if [ -L "$dst_dir/config" ]; then
          $DRY_RUN_CMD rm -f "$dst_dir/config"
        fi
        if [ -L "$dst_dir/private_key.pem" ]; then
          $DRY_RUN_CMD rm -f "$dst_dir/private_key.pem"
        fi

        $DRY_RUN_CMD ${pkgs.coreutils}/bin/install -m 600 -T "$src_cfg" "$dst_dir/config"
        $DRY_RUN_CMD ${pkgs.coreutils}/bin/install -m 600 -T "$src_key" "$dst_dir/private_key.pem"
      '';

  home.activation.materializeGhHosts =
    lib.hm.dag.entryAfter
      (
        if pkgs.stdenv.isDarwin then
          [ "materializeDarwinSopsSecrets" ]
        else
          [ "materializeLinuxSopsSecrets" ]
      )
      ''
                set -euo pipefail

                export PATH="${pkgs.coreutils}/bin:/usr/bin:/bin:/usr/sbin:/sbin:''${PATH:-}"

                token_file=""
                for candidate in \
                  /run/secrets/github-token \
                  /var/run/secrets/github-token \
                  "$HOME/.local/share/sops-nix/secrets/github-token" \
                  "$HOME/.config/sops-nix/secrets/github-token"
                do
                  if [ -r "$candidate" ]; then
                    token_file="$candidate"
                    break
                  fi
                done

                if [ -z "$token_file" ]; then
                  echo "warning: github-token secret is missing; skipping gh hosts materialization" >&2
                  exit 0
                fi

                github_token="$(${pkgs.coreutils}/bin/tr -d '\n\r' < "$token_file")"
                if [ -z "$github_token" ]; then
                  echo "warning: github-token secret is empty; skipping gh hosts materialization" >&2
                  exit 0
                fi

                gh_dir="$HOME/.config/gh"
                gh_hosts="$gh_dir/hosts.yml"
                tmp_file="$(${pkgs.coreutils}/bin/mktemp)"

                cleanup() {
                  rm -f "$tmp_file"
                }
                trap cleanup EXIT

                $DRY_RUN_CMD mkdir -p "$gh_dir"
                cat >"$tmp_file" <<EOF
        github.com:
            users:
                ${config.home.username}:
                    oauth_token: $github_token
            git_protocol: ssh
            user: ${config.home.username}
            oauth_token: $github_token
        EOF

                $DRY_RUN_CMD ${pkgs.coreutils}/bin/install -m 600 -T "$tmp_file" "$gh_hosts"
      '';

  home.file = lib.mkMerge [
    {
      ".ssh/fortress_rsa".source =
        config.lib.file.mkOutOfStoreSymlink config.sops.secrets.fortress_rsa.path;
      ".ssh/github_ed25519".source =
        config.lib.file.mkOutOfStoreSymlink config.sops.secrets.github_ed25519.path;
      ".ssh/id_ed25519".source =
        config.lib.file.mkOutOfStoreSymlink config.sops.secrets.cdenneen_ed25519_2024.path;
      ".ssh/cdenneen_ed25519_2024".source =
        config.lib.file.mkOutOfStoreSymlink config.sops.secrets.cdenneen_ed25519_2024.path;
      ".ssh/codecommit_rsa".source =
        config.lib.file.mkOutOfStoreSymlink config.sops.secrets.codecommit_rsa.path;
      ".ssh/id_rsa_cloud9".source =
        config.lib.file.mkOutOfStoreSymlink config.sops.secrets.id_rsa_cloud9.path;

      ".ssh/fortress_rsa.pub".source = ../../../../pub/ssh/fortress_rsa.pub;
      ".ssh/github_ed25519.pub".source = ../../../../pub/ssh/github_ed25519.pub;
      ".ssh/id_ed25519.pub".source = ../../../../pub/ssh/id_ed25519.pub;
      ".ssh/cdenneen_ed25519_2024.pub".source = ../../../../pub/ssh/cdenneen_ed25519_2024.pub;
      ".ssh/codecommit_rsa.pub".source = ../../../../pub/ssh/codecommit_rsa.pub;
      ".ssh/id_rsa_cloud9.pub".source = ../../../../pub/ssh/id_rsa_cloud9.pub;
    }

    {
      ".opencode/opencode.json".text = opencodeConfigJson;
    }

    {
      ".local/bin/update-secrets" = {
        source = ./files/update-secrets;
        executable = true;
      };

      ".local/bin/ssh-add-keys" = {
        source = ./files/ssh-add-keys;
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
    })
  ];

  home.activation.codexTelegramNotifyInstall = lib.mkIf isNyx (
    lib.hm.dag.entryAfter [ "writeBoundary" ] ''
      set -euo pipefail

      src="${config.sops.secrets.opencode_telegram_notify_ts.path}"
      ts_dst="$HOME/.codex/plugins/telegram-notify.ts"

      $DRY_RUN_CMD mkdir -p "$HOME/.codex/plugins"

      if [ -L "$ts_dst" ]; then
        $DRY_RUN_CMD rm -f "$ts_dst"
      fi

      if [ -r "$src" ]; then
        $DRY_RUN_CMD ${pkgs.coreutils}/bin/install -m 600 -T "$src" "$ts_dst"
      fi
    ''
  );

  # If a local user override exists for opencode-serve.service, it can shadow the
  # system-managed user unit (defined in NixOS) and break PATH/MCP startup.
  home.activation.opencodeNyxCleanupUserUnit = lib.mkIf isNyx (
    lib.hm.dag.entryAfter [ "reloadSystemd" ] ''
      set -euo pipefail

      unit="$HOME/.config/systemd/user/opencode-serve.service"
      dropin_dir="$HOME/.config/systemd/user/opencode-serve.service.d"

      if [ -e "$unit" ] || [ -d "$dropin_dir" ]; then
        ${pkgs.systemd}/bin/systemctl --user stop opencode-serve.service 2>/dev/null || true
        $DRY_RUN_CMD rm -f "$unit"
        $DRY_RUN_CMD rm -rf "$dropin_dir"
        ${pkgs.systemd}/bin/systemctl --user daemon-reload 2>/dev/null || true
        ${pkgs.systemd}/bin/systemctl --user start opencode-serve.service 2>/dev/null || true
      fi
    ''
  );

  programs.zsh.initExtra = lib.mkAfter ''
    if [ -r "${config.sops.secrets.supabase_access_token.path}" ]; then
      export SUPABASE_ACCESS_TOKEN="$(${pkgs.coreutils}/bin/tr -d '\n\r' < "${config.sops.secrets.supabase_access_token.path}")"
    fi

    if [ -r "${config.sops.secrets.cloudflare_account_api_token.path}" ]; then
      export CLOUDFLARE_API_TOKEN="$(${pkgs.coreutils}/bin/tr -d '\n\r' < "${config.sops.secrets.cloudflare_account_api_token.path}")"
      export CF_API_TOKEN="$CLOUDFLARE_API_TOKEN"
    fi

    export CLOUDFLARE_ACCOUNT_ID="19a23ecf9ba79236ab8e64c8c7bf3507"
    export CF_ACCOUNT_ID="$CLOUDFLARE_ACCOUNT_ID"
    export CLOUDFLARE_ZONE_NAME="denneen.net"

    if [ -r "${config.sops.secrets.gemini_api_key.path}" ]; then
      gemini_api_key="$(${pkgs.coreutils}/bin/tr -d '\n\r' < "${config.sops.secrets.gemini_api_key.path}")"
      if [ -n "$gemini_api_key" ]; then
        export GEMINI_API_KEY="$gemini_api_key"
        export GOOGLE_API_KEY="$gemini_api_key"
      fi
    fi
  '';

  programs.bash.initExtra = lib.mkAfter ''
    if [ -r "${config.sops.secrets.supabase_access_token.path}" ]; then
      export SUPABASE_ACCESS_TOKEN="$(${pkgs.coreutils}/bin/tr -d '\n\r' < "${config.sops.secrets.supabase_access_token.path}")"
    fi

    if [ -r "${config.sops.secrets.cloudflare_account_api_token.path}" ]; then
      export CLOUDFLARE_API_TOKEN="$(${pkgs.coreutils}/bin/tr -d '\n\r' < "${config.sops.secrets.cloudflare_account_api_token.path}")"
      export CF_API_TOKEN="$CLOUDFLARE_API_TOKEN"
    fi

    export CLOUDFLARE_ACCOUNT_ID="19a23ecf9ba79236ab8e64c8c7bf3507"
    export CF_ACCOUNT_ID="$CLOUDFLARE_ACCOUNT_ID"
    export CLOUDFLARE_ZONE_NAME="denneen.net"

    if [ -r "${config.sops.secrets.gemini_api_key.path}" ]; then
      gemini_api_key="$(${pkgs.coreutils}/bin/tr -d '\n\r' < "${config.sops.secrets.gemini_api_key.path}")"
      if [ -n "$gemini_api_key" ]; then
        export GEMINI_API_KEY="$gemini_api_key"
        export GOOGLE_API_KEY="$gemini_api_key"
      fi
    fi
  '';
}
