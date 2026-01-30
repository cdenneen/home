{
  config,
  lib,
  pkgs,
  system,
  osConfig ? null,
  ...
}:
let
  cfg = config.profiles;
  isWsl = osConfig != null && ((osConfig.wsl.enable or false) == true);

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
in
{
  options.profiles.cdenneen.enable = lib.mkEnableOption "Enable cdenneen profile";

  config = lib.mkIf cfg.cdenneen.enable {
    home.sessionVariables.EDITOR = "nvim";
    home.sessionVariables.XDG_DATA_HOME = "${config.home.homeDirectory}/.local/share";
    home.sessionVariables.XDG_CACHE_HOME = "${config.home.homeDirectory}/.cache";
    programs = {
      alacritty.enable = cfg.gui.enable;
      atuin.enable = true;
      autojump.enable = true;
      awscli = {
        enable = true;
        credentials = {
          "nextology" = {
            credential_process = "sh -c \"op --account=ap --vault=GSS item get --format=json --fields=label=AccessKeyId,label=SecretAccessKey nextology | jq 'map({key: .label, value: .value}) | from_entries + {Version: 1}'\"";
          };
        };
      };
      bash = {
        enable = true;
        historyControl = [
          "ignoredups"
          "ignorespace"
        ];

        shellAliases = {
          ga = "git add";
          gc = "git commit";
          gco = "git checkout";
          gcp = "git cherry-pick";
          gdiff = "git diff";
          gl = "git prettylog";
          gp = "git push";
          gs = "git status";
          gt = "git tag";
        };
      };
      direnv = {
        enable = true;
        enableZshIntegration = true;
        nix-direnv.enable = true;
        config = {
          global = {
            load_dotenv = true;
          };
        };
      };
      wezterm.enable = if system != "x86_64-darwin" then cfg.gui.enable else false;
      kitty.enable = cfg.gui.enable;
      git = {
        enable = true;
        settings = {
          user = {
            name = "Chris Denneen";
            email = "cdenneen@gmail.com";
            signingkey = "0xBFEB75D960DFAA6B";
          };
          github.user = "cdenneen";
        };
        ignores = [
          ".DS_Store"
          "Thumbs.db"
        ];
        includes = [
          {
            condition = "gitdir:~/src/ap";
            contents = {
              user = {
                name = "Christopher Denneen";
                email = "CDenneen@ap.org";
                signingkey = "0x3834814930B83A30";
              };
              commit.gpgsign = true;
            };
          }
          {
            condition = "gitdir:~/src/personal";
            contents = {
              user = {
                name = "Chris Denneen";
                email = "cdenneen@gmail.com";
                signingkey = "0xBFEB75D960DFAA6B";
              };
              commit.gpgsign = true;
            };
          }
        ];
      };

      gpg = {
        enable = true;
        publicKeys = [
          {
            source = ../../../secrets/personal.pub;
            trust = 5;
          }
          {
            source = ../../../secrets/work.pub;
            trust = 5;
          }
        ];
      };
      zoxide = {
        options = [
          "--cmd cd"
        ];
      };
      zsh = {
        loginExtra = builtins.readFile ./cdenneen/zlogin;
        logoutExtra = builtins.readFile ./cdenneen/zlogout;
        shellAliases = {
          c = "clear";
          e = "$EDITOR";
          se = "sudoedit";
          ec = "nvim --cmd ':lua vim.g.noplugins=1' "; # nvim --clean
          g = "git";

          ga = "git add";
          gb = "git branch";
          gc = "git commit";
          gcm = "git commit -m";
          gco = "git checkout";
          gcob = "git checkout -b";
          gcp = "git cherry-pick";
          gd = "git diff";
          gdiff = "git diff";
          gf = "git fetch";
          gl = "git prettylog";
          gm = "git merge";
          gp = "git push";
          gpr = "git pull --rebase";
          gr = "git rebase -i";
          gs = "git status -sb";
          gt = "git tag";
          gu = "git reset @ --"; # think git unstage
          gx = "git reset --hard @";

          jf = "jj git fetch";
          jn = "jj new";
          js = "jj st";

          k = "kubectl";
          kprod = "switch eks_eks-prod-us-east-1-prod-2-use1/eks_prod-2-use1";
          kshared = "switch eks_eks-apss-us-east-1-shared-1-use1/eks_shared-1-use1";
          kinteract = "switch eks_eks-prod-us-east-1-apinteractives-datateam/eks_apinteractives-datateam";
          kinteractdr = "switch eks_eks-prod-us-west-2-apinteractives-datateam-dr/eks_apinteractives-datateam-dr";

          vi = "nvim";
          vim = "nvim";
          sso = "aws sso login --profile sso-apss --no-browser --use-device-code";
          ssod = "aws sso login --profile sso-capdev --no-browser --use-device-code";
          ssoq = "aws sso login --profile sso-awsqa --no-browser --use-device-code";
          ssop = "aws sso login --profile sso-awsprod --no-browser --use-device-code";
          swnix =
            if pkgs.stdenv.isDarwin then
              "darwin-rebuild switch --flake github:cdenneen/nixos-config#mac"
            else
              "sudo nixos-rebuild switch --flake github:cdenneen/nixos-config#vm-aarch64-utm";

        }
        // (if pkgs.stdenv.isLinux then { } else { });
      };
      ssh =
        let
          identityConfig = {
            identitiesOnly = true;
            identityFile = [
              config.sops.secrets.fortress_rsa.path
              config.sops.secrets.cdenneen_ed25519_2024.path
              config.sops.secrets.codecommit_rsa.path
              config.sops.secrets.id_rsa_cloud9.path
              config.sops.secrets.cdenneen_github.path
            ];
          };
          proxyCommand = "${pkgs.dash}/bin/dash -c \"${pkgs.awscli2}/bin/aws ssm start-session --target %h --document-name AWS-StartSSHSession --parameters 'portNumber=%p'\"";
        in
        {
          enable = true;
          matchBlocks = {
            "i-* m-*" = {
              proxyCommand = proxyCommand;
            };
            "c9" = identityConfig // {
              proxyCommand = proxyCommand;
              user = "ubuntu";
              hostname = "i-085b4f08b56c8b914";
            };
            "eros-ssm" = identityConfig // {
              proxyCommand = proxyCommand;
              user = "cdenneen";
              hostname = "i-0a3e1df60bde023ad";
              remoteForwards = erosRemoteForwards;
            };
            "eros" = identityConfig // {
              user = "cdenneen";
              hostname = "10.224.11.147";
              remoteForwards = erosRemoteForwards;
            };
            "git-codecommit.*.amazonaws.com" = identityConfig // {
              user = "APKA4GUE2SGMGTPZB44D";
            };
            "puppet" = identityConfig // {
              user = "root";
              hostname = "ctcpmaster01.ap.org";
            };
            "github.com" = identityConfig;
            "gitlab.com" = identityConfig;
            "git.ap.org" = identityConfig;
          };
        };
    };
    home.packages =
      with pkgs;
      lib.optionals config.profiles.gui.enable [
        # ] ++ lib.optionals ( system != "aarch64-linux" and config.profiles.gui.enable) [
        #   discord
        #   spotify
      ]
      ++ [
        fzf
        kubeswitch
        eks-node-viewer
        fluxcd
        _1password-cli
        lemonade
        netcat-openbsd
        xsel
      ];
    catppuccin = {
      flavor = "mocha";
      accent = "blue";
    };
    launchd.agents.lemonade = lib.mkIf pkgs.stdenv.isDarwin {
      enable = true;
      config = {
        ProgramArguments = [
          "${pkgs.lemonade}/bin/lemonade"
          "server"
        ];
        KeepAlive = true;
        RunAtLoad = true;
      };
    };

    systemd.user.services.wsl-clipboard-bridge = lib.mkIf isWsl {
      Unit = {
        Description = "WSL clipboard bridge for SSH remotes";
        After = [ "default.target" ];
      };

      Service = {
        Restart = "always";
        ExecStart =
          let
            socat = "${pkgs.socat}/bin/socat";
            tr = "${pkgs.coreutils}/bin/tr";
          in
          "${pkgs.bash}/bin/bash -lc ${lib.escapeShellArg ''
            ${socat} TCP-LISTEN:2491,fork,reuseaddr SYSTEM:\"clip.exe\" &
            ${socat} TCP-LISTEN:2492,fork,reuseaddr SYSTEM:\"powershell.exe -NoProfile -Command \\\"[Console]::OutputEncoding=[System.Text.Encoding]::UTF8; Get-Clipboard -Raw\\\" | ${tr} -d '\\\\r'\" &
            wait
          ''}";
      };

      Install = {
        WantedBy = [ "default.target" ];
      };
    };

    home.file = {
      ".kube/switch-config.yaml".source = ./cdenneen/switch-config.yaml;
    };
    home.file.".aws/config.init" = {
      text = builtins.readFile ./cdenneen/aws_config;
      onChange = ''
        cat ~/.aws/config.init > ~/.aws/config
        chmod 600 ~/.aws/config
      '';
      force = true;
    };
    home.activation.awsConfigEc2Patch = lib.hm.dag.entryAfter [ "writeBoundary" ] ''
      if [[ -f "$HOME/.aws/config" ]]; then
        # EC2 detection
        if [[ -r /sys/devices/virtual/dmi/id/sys_vendor ]] && grep -qi "amazon" /sys/devices/virtual/dmi/id/sys_vendor; then
          echo "EC2 detected — swapping source_profile"

          sed -i 's/source_profile=sso-apss/source_profile=ec2-local/g' "$HOME/.aws/config"

          if ! grep -q "^\[profile ec2-local\]" "$HOME/.aws/config"; then
            echo "[profile ec2-local]" >> "$HOME/.aws/config"
            echo "credential_source = Ec2InstanceMetadata" >> "$HOME/.aws/config"
          fi
        fi
      fi
    '';
    xdg.configFile = {
      "direnv/lib/k8s_context.sh".text = builtins.readFile ./cdenneen/k8s_context.sh;
      "zsh".source = ./cdenneen/zsh;
      "nvim".source =
        config.lib.file.mkOutOfStoreSymlink "${config.home.homeDirectory}/src/personal/nvim";
    };
    sops.secrets = {
      "op_config" = {
        # Don't symlink as causes error so copy manually from sops-nix
        # path = "${config.home.homeDirectory}/.config/op/config";
        mode = "0400";
      };
      "cdenneen_github" = {
        path = "${config.home.homeDirectory}/.ssh/id_ed25519_github";
        mode = "0400";
      };
      "cdenneen_github_pub" = {
        path = "${config.home.homeDirectory}/.ssh/id_ed25519_github.pub";
        mode = "0400";
      };
      "fortress_rsa" = {
        path = "${config.home.homeDirectory}/.ssh/id_rsa.fortress";
        mode = "0400";
      };
      "cdenneen_ed25519_2024" = {
        path = "${config.home.homeDirectory}/.ssh/cdenneen_ed25519_2024.pem";
        mode = "0400";
      };
      "codecommit_rsa" = {
        path = "${config.home.homeDirectory}/.ssh/codecommit_rsa";
        mode = "0400";
      };
      "id_rsa_cloud9" = {
        path = "${config.home.homeDirectory}/.ssh/id_rsa_cloud9";
        mode = "0400";
      };
      "gpg_gmail" = {
        path = "${config.home.homeDirectory}/.gnupg/private-keys-v1.d/personal.key";
        mode = "0400";
      };
      "gpg_ap" = {
        path = "${config.home.homeDirectory}/.gnupg/private-keys-v1.d/work.key";
        mode = "0400";
      };
      "gpg_gmail_pub" = {
        path = "${config.home.homeDirectory}/.gnupg/personal.key.pub";
        mode = "0400";
      };
      "gpg_ap_pub" = {
        path = "${config.home.homeDirectory}/.gnupg/work.key.pub";
        mode = "0400";
      };
    };
  };
}
