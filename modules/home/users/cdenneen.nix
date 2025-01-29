{
  config,
  lib,
  pkgs,
  system,
  ...
}:
let
  cfg = config.profiles;
  # Import the common git configuration.
  sharedGitConfig = import ../programs/git.nix { inherit config pkgs lib; };
  # Access the programs attribute directly, ensuring it's unwrapped properly
  # gitConfig = sharedGitConfig.config.content.programs.git;
  # gitExtraConfig = gitConfig.extraConfig;
  gitExtraConfig = sharedGitConfig.config.content.programs.git.extraConfig;
in
{
  options.profiles.cdenneen.enable = lib.mkEnableOption "Enable cdenneen profile";

  config = lib.mkIf cfg.cdenneen.enable {
    home.sessionVariables.EDITOR = "nvim";
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
        historyControl = [ "ignoredups" "ignorespace" ];

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
        nix-direnv.enable = true;
        config = {
          global = { load_dotenv = true; };
        };
      };
      vscode.enable = cfg.gui.enable;
      wezterm.enable = cfg.gui.enable;
      kitty.enable = cfg.gui.enable;
      git = {
        enable = true;
        userName = "Chris Denneen";
        userEmail = "cdenneen@gmail.com";
        ignores = [ ".DS_Store" "Thumbs.db" ];
        extraConfig = gitExtraConfig // { 
          github.user = "cdenneen";
        };
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
      keychain = {
        keys = [
          "~/.ssh/id_ed25519"
          "0x3834814930B83A30"
          "0xBFEB75D960DFAA6B"
        ];
      };
      gpg = {
        enable = true;
        publicKeys = [
          { source = ../../../secrets/personal.pub; trust = 5; }
          { source = ../../../secrets/work.pub; trust = 5; }
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
          ec = "nvim --cmd ':lua vim.g.noplugins=1' "; #nvim --clean
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
          swnix = if pkgs.stdenv.isDarwin then "darwin-rebuild switch --flake github:cdenneen/nixos-config#mac" else "sudo nixos-rebuild switch --flake github:cdenneen/nixos-config#vm-aarch64-utm";
    
        } // (if pkgs.stdenv.isLinux then {
          # Two decades of using a Mac has made this such a strong memory
          # that I'm just going to keep it consistent.
          pbcopy = "xsel";
          pbpaste = "xsel -o";
        } else {});
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
          };
          "eros" = identityConfig // {
            user = "cdenneen";
            hostname = "10.224.11.147";
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
      ] ++ [
        kubeswitch
        eks-node-viewer
        fluxcd
      ];
    catppuccin = {
      flavor = "mocha";
      accent = "blue";
    };
    home.file = {
      ".kube/switch-config.yaml".source = ./cdenneen/switch-config.yaml;
    };
    xdg.configFile = {
      "direnv/lib/k8s_context.sh".text = builtins.readFile ./cdenneen/k8s_context.sh;
      "zsh".source = ./cdenneen/zsh;
    };
    sops.secrets = {
      "op_config" = {
        path = "${config.home.homeDirectory}/.config/op/config";
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
      "aws-config" = {
        path = "${config.home.homeDirectory}/.aws/config";
        mode = "0440";
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
