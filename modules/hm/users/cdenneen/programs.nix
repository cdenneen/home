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
      config.sops.secrets.fortress_rsa.path
      config.sops.secrets.cdenneen_ed25519_2024.path
      config.sops.secrets.codecommit_rsa.path
      config.sops.secrets.id_rsa_cloud9.path
      config.sops.secrets.github_ed25519.path
    ];
  };

  ssmProxyCommand = "${pkgs.dash}/bin/dash -c \"PATH=${pkgs.ssm-session-manager-plugin}/bin:${pkgs.awscli2}/bin:$PATH ${pkgs.awscli2}/bin/aws ssm start-session --target %h --document-name AWS-StartSSHSession --parameters 'portNumber=%p'\"";
in

{
  home.packages =
    with pkgs;
    [
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

      # Kubernetes / cloud CLI
      kubectl
      kubernetes-helm
      kubeswitch
      eks-node-viewer
      fluxcd
      pkgs."fluxcd-operator"
      glab
      gh

      # Runtimes / cloud
      nodejs_24 # LTS
      yarn
      _1password-cli
      awscli2
      ssm-session-manager-plugin
      oauth2-proxy

      # Clipboard
      lemonade
    ]
    ++ lib.optionals pkgs.stdenv.isLinux [
      netcat-openbsd
      xsel
    ]
    ++ lib.optionals (pkgs.stdenv.isLinux && hostName == "nyx") [
      chromium
      firefox
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
        hostname = "10.224.11.38";
        remoteForwards = erosRemoteForwards;
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
        identityFile = [ config.sops.secrets.github_ed25519.path ];
      };

      "gitlab.com" = identityConfig;
      "git.ap.org" = identityConfig // {
        identitiesOnly = true;
        identityFile = [ "~/.ssh/id_ed25519" ];
      };
    };
  };

  # glab requires strict perms on its config file (0600). Home Manager's
  # `home.file.<name>.text` produces a store symlink (read-only), so we write a
  # source file and then copy+chmod it at activation time.
  home.file.".config/glab-cli/config.yml.source".text = ''
    git_protocol: ssh
    editor:
    browser:
    glamour_style: dark
    check_update: true
    display_hyperlinks: false
    host: git.ap.org
    no_prompt: false
    telemetry: false

    hosts:
      gitlab.com:
        api_protocol: https
        git_protocol: ssh
        user: cdenneen
        container_registry_domains:
          - gitlab.com
          - gitlab.com:443
          - registry.gitlab.com

      git.ap.org:
        api_protocol: https
        git_protocol: ssh
        user: cdenneen
        container_registry_domains:
          - git.ap.org
          - git.ap.org:443
          - registry.associatedpress.com
  '';

  home.activation.glabCliConfigPerms = lib.hm.dag.entryAfter [ "writeBoundary" ] ''
    $DRY_RUN_CMD mkdir -p "$HOME/.config/glab-cli"
    $DRY_RUN_CMD cp -f "$HOME/.config/glab-cli/config.yml.source" "$HOME/.config/glab-cli/config.yml"
    $DRY_RUN_CMD chmod 600 "$HOME/.config/glab-cli/config.yml"
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
}
