{
  config,
  lib,
  osConfig ? null,
  pkgs,
  ...
}:

let
  isWsl = osConfig != null && ((osConfig.wsl.enable or false) == true);
  hostName = if osConfig != null then (osConfig.networking.hostName or "") else "";
  isGhost = hostName == "ghost";
  isMbair = hostName == "mbair";
  enableOciGhostAutostart = false;
  sshDir = "${config.home.homeDirectory}/.ssh";
  # Prefer the macOS lemonade server over a local Linux server so SSH
  # remote forwarding to 127.0.0.1:2489 works without port conflicts.
  enableLinuxLemonadeServer = false;

  erosRemoteForwards =
    if pkgs.stdenv.isDarwin then
      [
        {
          bind.port = 2489;
          host.address = "127.0.0.1";
          host.port = 2489;
        }
      ]
    else if isWsl then
      [
        {
          bind.port = 2491;
          host.address = "127.0.0.1";
          host.port = 2491;
        }
        {
          bind.port = 2492;
          host.address = "127.0.0.1";
          host.port = 2492;
        }
      ]
    else
      [ ];

  identityConfig = {
    identitiesOnly = true;
    identityFile = [
      "${sshDir}/fortress_rsa"
      "${sshDir}/cdenneen_ed25519_2024"
      "${sshDir}/codecommit_rsa"
      "${sshDir}/id_rsa_cloud9"
      "${sshDir}/github_ed25519"
    ];
  };

  ssmProxyCommand = "${pkgs.dash}/bin/dash -c \"PATH=${pkgs.ssm-session-manager-plugin}/bin:${pkgs.awscli2}/bin:$PATH ${pkgs.awscli2}/bin/aws ssm start-session --target %h --document-name AWS-StartSSHSession --parameters 'portNumber=%p'\"";

  hmCorePackages = with pkgs; [
    # Shell / UX
    atuin
    bat
    jq
    ripgrep
    fzf
    zoxide
    eza
    direnv
    starship
    tmux

    # Git + auth
    glab
    gh
    _1password-cli

    # Remote access
    awscli2
    ssm-session-manager-plugin
    oci-cli

    # Workspace / repo flow
    (pkgs.callPackage ../../../../pkgs/rtk.nix { })
    (pkgs.callPackage ../../../../pkgs/update-workspace-agents.nix { })
    (pkgs.callPackage ../../../../pkgs/workspace-init.nix { })
    (pkgs.callPackage ../../../../pkgs/setup-repo.nix { })
    (pkgs.callPackage ../../../../pkgs/update-workspace.nix { })
  ];

  hmHeavyPackages = with pkgs; [
    # Kubernetes / GitOps
    kubectl
    kubernetes-helm
    kubeswitch
    eks-node-viewer
    fluxcd
    pkgs."fluxcd-operator"
    yq-go
    kustomize
    kubeconform

    # Extra runtime and local bridge tools
    nodejs_24
    yarn
    oauth2-proxy
    lemonade
  ];
in

{
  home.packages =
    hmCorePackages
    ++ lib.optionals (!(isGhost || isMbair)) hmHeavyPackages
    ++ lib.optionals pkgs.stdenv.isLinux [
      pkgs.netcat-openbsd
    ]
    ++ lib.optionals (pkgs.stdenv.isLinux && !isGhost) [
      pkgs.xsel
    ]
    ++ lib.optionals (pkgs.stdenv.isLinux && hostName == "nyx") [
      pkgs.chromium
      pkgs.firefox
    ];

  programs.nh = {
    enable = true;
    flake = "${config.home.homeDirectory}/src/workspace/nix/home";
    clean.enable = false;
  };

  home.sessionVariables = lib.mkMerge [
    (lib.mkIf (pkgs.stdenv.isLinux && !isWsl) {
      LEMONADE_SERVER = "127.0.0.1:2489";
    })
  ];

  launchd.agents.lemonade = lib.mkIf pkgs.stdenv.isDarwin {
    enable = true;
    config = {
      ProgramArguments = [
        "${pkgs.lemonade}/bin/lemonade"
        "server"
      ];
      EnvironmentVariables = {
        PATH = "/usr/bin:/bin:/usr/sbin:/sbin:/usr/local/bin";
      };
      KeepAlive = true;
      RunAtLoad = true;
    };
  };

  launchd.agents.opencode-serve = lib.mkIf pkgs.stdenv.isDarwin {
    enable = true;
    config = {
      ProgramArguments = [
        "${config.home.profileDirectory}/bin/opencode"
        "serve"
        "--hostname"
        "127.0.0.1"
        "--port"
        "4097"
      ];
      EnvironmentVariables = {
        HOME = config.home.homeDirectory;
        PATH = "${config.home.profileDirectory}/bin:${config.home.homeDirectory}/.nix-profile/bin:/nix/var/nix/profiles/default/bin:/run/current-system/sw/bin:/etc/profiles/per-user/cdenneen/bin:/usr/bin:/bin:/usr/sbin:/sbin:/opt/homebrew/bin";
      };
      KeepAlive = true;
      RunAtLoad = true;
      ProcessType = "Background";
      StandardOutPath = "${config.home.homeDirectory}/Library/Logs/opencode-serve.log";
      StandardErrorPath = "${config.home.homeDirectory}/Library/Logs/opencode-serve.log";
    };
  };

  launchd.agents.oci-ghost-autostart = lib.mkIf (pkgs.stdenv.isDarwin && enableOciGhostAutostart) {
    enable = true;
    config = {
      ProgramArguments = [
        "${config.home.homeDirectory}/.local/bin/ensure-oci-ghost-runner"
      ];
      EnvironmentVariables = {
        HOME = config.home.homeDirectory;
        WORKSPACE_ROOT = "${config.home.homeDirectory}/code/workspace";
        PATH = "${config.home.profileDirectory}/bin:${config.home.homeDirectory}/.nix-profile/bin:/nix/var/nix/profiles/default/bin:/run/current-system/sw/bin:/etc/profiles/per-user/cdenneen/bin:/usr/bin:/bin:/usr/sbin:/sbin:/opt/homebrew/bin";
      };
      RunAtLoad = true;
      StartInterval = 300;
      ProcessType = "Background";
      StandardOutPath = "${config.home.homeDirectory}/Library/Logs/oci-ghost-autostart.log";
      StandardErrorPath = "${config.home.homeDirectory}/Library/Logs/oci-ghost-autostart.log";
    };
  };

  launchd.agents.peps-service = lib.mkIf pkgs.stdenv.isDarwin {
    enable = true;
    config = {
      ProgramArguments = [
        "${config.home.homeDirectory}/.local/bin/ensure-peps-runner"
      ];
      EnvironmentVariables = {
        HOME = config.home.homeDirectory;
        WORKSPACE_ROOT = "${config.home.homeDirectory}/code/workspace";
        PATH = "${config.home.profileDirectory}/bin:${config.home.homeDirectory}/.nix-profile/bin:/nix/var/nix/profiles/default/bin:/run/current-system/sw/bin:/etc/profiles/per-user/cdenneen/bin:/usr/bin:/bin:/usr/sbin:/sbin:/opt/homebrew/bin";
      };
      RunAtLoad = true;
      KeepAlive = true;
      ProcessType = "Background";
      StandardOutPath = "${config.home.homeDirectory}/Library/Logs/peps-service.log";
      StandardErrorPath = "${config.home.homeDirectory}/Library/Logs/peps-service.log";
    };
  };

  systemd.user.services.lemonade = lib.mkIf enableLinuxLemonadeServer {
    Unit = {
      Description = "Lemonade clipboard server";
      After = [ "default.target" ];
    };
    Service = {
      Restart = "always";
      ExecStart = "${pkgs.lemonade}/bin/lemonade server";
    };
    Install = {
      WantedBy = [ "default.target" ];
    };
  };

  systemd.user.services.wsl-clipboard-bridge = lib.mkIf isWsl {
    Unit = {
      Description = "WSL clipboard bridge for SSH remotes";
      After = [ "default.target" ];
    };

    Service =
      let
        copyScript = pkgs.writeShellScript "wsl-clipboard-copy" ''
          set -euo pipefail
          cat | clip.exe
        '';

        pasteScript = pkgs.writeShellScript "wsl-clipboard-paste" ''
          set -euo pipefail
          powershell.exe -NoProfile -NonInteractive -Command '
            [Console]::OutputEncoding=[System.Text.Encoding]::UTF8
            $t = Get-Clipboard -Raw
            if ($null -ne $t) {
              $t -replace "`r", ""
            }
          '
        '';

        script = pkgs.writeShellScript "wsl-clipboard-bridge" ''
          set -euo pipefail

          ${pkgs.socat}/bin/socat TCP-LISTEN:2491,fork,reuseaddr EXEC:"${copyScript}" &
          ${pkgs.socat}/bin/socat TCP-LISTEN:2492,fork,reuseaddr EXEC:"${pasteScript}" &

          wait
        '';
      in
      {
        Restart = "always";
        ExecStart = "${script}";
      };

    Install = {
      WantedBy = [ "default.target" ];
    };
  };

  services.syncthing = {
    enable = true;
    tray.enable = pkgs.stdenv.isLinux;
    overrideDevices = false;
    overrideFolders = false;
    settings = {
      options = {
        urAccepted = -1;
      };
    };
  };

  programs = {
    atuin.enable = true;
    fzf.enable = true;
    zoxide.enable = true;
    direnv.enable = true;
    direnv.nix-direnv.enable = true;
    starship.enable = true;

    awscli = {
      enable = true;
      package = pkgs.awscli2;
      credentials = {
        nextology = {
          credential_process = ''sh -c "op --account=ap --vault=GSS item get --format=json --fields=label=AccessKeyId,label=SecretAccessKey nextology | jq 'map({key: .label, value: .value}) | from_entries + {Version: 1}'"'';
        };
      };
    };
    # glab config is managed explicitly via home.file
  };

  programs.ssh = {
    enable = true;
    matchBlocks = {
      "i-* m-*" = {
        proxyCommand = ssmProxyCommand;
      };

      c9 = identityConfig // {
        proxyCommand = ssmProxyCommand;
        user = "ubuntu";
        hostname = "i-085b4f08b56c8b914";
      };

      "eros-ssm" = identityConfig // {
        proxyCommand = ssmProxyCommand;
        user = "cdenneen";
        hostname = "i-0a3e1df60bde023ad";
        remoteForwards = erosRemoteForwards;
      };

      eros = identityConfig // {
        user = "cdenneen";
        hostname = "10.224.11.147";
        remoteForwards = erosRemoteForwards;
      };

      nyx = identityConfig // {
        user = "cdenneen";
        hostname = "100.80.58.4";
        remoteForwards = erosRemoteForwards;
      };

      ghost = identityConfig // {
        user = "cdenneen";
        hostname = "150.136.97.147";
      };

      "nyx-ssm" = identityConfig // {
        proxyCommand = ssmProxyCommand;
        user = "cdenneen";
        hostname = "i-052cb7906e89d224a";
        remoteForwards = erosRemoteForwards;
      };

      nix = {
        user = "root";
        hostname = "10.224.11.140";
        identityFile = "~/.ssh/cdenneen_winlaptop.pem";
        extraOptions.RequestTTY = "no";
      };

      "git-codecommit.*.amazonaws.com" = identityConfig // {
        user = "APKA4GUE2SGMGTPZB44D";
      };

      puppet = identityConfig // {
        user = "root";
        hostname = "ctcpmaster01.ap.org";
      };

      "github.com" = {
        user = "git";
        identitiesOnly = true;
        identityFile = [ "${sshDir}/github_ed25519" ];
      };

      "gitlab.com" = {
        identitiesOnly = true;
        identityFile = [ "${sshDir}/cdenneen_ed25519_2024" ];
        user = "git";
      };
      "git.ap.org" = identityConfig // {
        identitiesOnly = true;
        identityFile = [ "~/.ssh/id_ed25519" ];
      };
    };
  };

  # glab config is sourced from SOPS secret `glab_cli_config` and written to
  # ~/.config/glab-cli/config.yml with mode 0600.

  # On macOS, older glab runs may leave a second config at
  # ~/Library/Application Support/glab-cli/config.yml, which triggers noisy
  # duplicate-config warnings. Keep one canonical config in ~/.config.
  home.activation.glabConfigConsolidateDarwin = lib.hm.dag.entryAfter [ "linkGeneration" ] ''
    if [ "$(uname -s)" = "Darwin" ]; then
      legacy_cfg="$HOME/Library/Application Support/glab-cli/config.yml"
      canonical_cfg="$HOME/.config/glab-cli/config.yml"

      if [ -e "$legacy_cfg" ]; then
        if [ -e "$canonical_cfg" ] && ${pkgs.diffutils}/bin/cmp -s "$legacy_cfg" "$canonical_cfg"; then
          $DRY_RUN_CMD rm -f "$legacy_cfg"
        else
          ts="$(${pkgs.coreutils}/bin/date +%Y%m%d%H%M%S)"
          $DRY_RUN_CMD mv "$legacy_cfg" "$legacy_cfg.bak-$ts"
        fi
      fi
    fi
  '';

  # Keep macOS as a Happier client only (no local happier-server), and ensure
  # the official nyx daemon launch agent is installed/running at login.
  home.activation.happierNyxDaemonDarwin = lib.hm.dag.entryAfter [ "setupLaunchAgents" ] ''
    if [ "$(uname -s)" = "Darwin" ]; then
      happier_bin="$(command -v happier || true)"
      if [ -n "$happier_bin" ] && [ -x "$happier_bin" ]; then
        export HAPPIER_HOME_DIR="$HOME/.happier"
        export HAPPIER_SERVER_URL="https://nyx.tail0e55.ts.net"
        export HAPPIER_WEBAPP_URL="https://nyx.tail0e55.ts.net"
        export HAPPIER_PUBLIC_SERVER_URL="https://nyx.tail0e55.ts.net"
        export HAPPIER_NO_BROWSER_OPEN=1
        export HAPPIER_DAEMON_WAIT_FOR_AUTH=1
        export HAPPIER_DAEMON_WAIT_FOR_AUTH_TIMEOUT_MS=0

        $DRY_RUN_CMD "$happier_bin" --server nyx daemon service install --json >/dev/null
        $DRY_RUN_CMD "$happier_bin" --server nyx daemon service start --json >/dev/null
      fi

      legacy_agent="$HOME/Library/LaunchAgents/org.nix-community.home.happier-daemon-nyx.plist"
      if [ -e "$legacy_agent" ]; then
        uid="$(${pkgs.coreutils}/bin/id -u)"
        $DRY_RUN_CMD /bin/launchctl bootout "gui/$uid/org.nix-community.home.happier-daemon-nyx" 2>/dev/null || true
        $DRY_RUN_CMD rm -f "$legacy_agent"
      fi
    fi
  '';

  # direnv loads this automatically (if present). Keep it tiny and just source
  # shared helpers so individual repos can assume they exist.
  home.file.".config/direnv/direnvrc".text = ''
    # -*- mode: sh -*-
    # shellcheck shell=bash

    if [ -r "$HOME/.config/direnv/lib/hm-nix-direnv.sh" ]; then
      source "$HOME/.config/direnv/lib/hm-nix-direnv.sh"
    fi

    if [ -r "$HOME/.config/direnv/lib/k8s_context.bash" ]; then
      source "$HOME/.config/direnv/lib/k8s_context.bash"
    fi
  '';

  home.file.".config/direnv/lib/k8s_context.bash".text = ''
    #!/usr/bin/env bash

    k8s_context() {
      local path="$1"

      if [ -f "$path/.context" ]; then
        source "$path"/.context
      fi
      if [ ! "$CONTEXT" ]; then
        # Split the path into an array using '/' as the delimiter
        IFS='/' read -ra array <<< "$path"

        # Get the length of the array
        local length=''${#array[@]}

        # Calculate the starting index to get the last three elements
        local start_index=$((length - 3))

        # Ensure start_index is not negative
        start_index=$((start_index >= 0 ? start_index : 0))

        # Extract the last three elements from the array
        last_three_elements=("''${array[@]:start_index}")

        case "''${last_three_elements[0]}" in
          capdev)
            ACCOUNT=dev
            ;;
          awsqa)
            ACCOUNT=qa
            ;;
          apss)
            ACCOUNT=apss
            ;;
          awsprod)
            ACCOUNT=prd
            ;;
        esac
        echo "$ACCOUNT"

        case "''${last_three_elements[1]}" in
          us-east-1)
            REGION=use1
            ;;
          us-west-2)
            REGION=usw2
            ;;
        esac
        echo "$REGION"
        CLUSTER_ID="''${last_three_elements[2]}"
        echo "$CLUSTER_ID"

        CONTEXT="eks_$ACCOUNT-$REGION-$CLUSTER_ID"
      fi
      echo "$CONTEXT"
      # export KUBECONFIG=$(switcher "$CONTEXT")
      KUBECONFIG=$(switcher "$CONTEXT" | sed 's/^__ //' | cut -d, -f1)
      export KUBECONFIG
    }
  '';

  # Telegram bridge wiring is configured per-host (see hosts/nixos/nyx.nix).
}
