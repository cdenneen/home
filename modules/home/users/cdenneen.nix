{
  config,
  lib,
  pkgs,
  ...
}:
let
  cfg = config.profiles;
  gitSharedConfig = import ../programs/git.nix { inherit config pkgs lib; };
  gitExtraConfig = gitSharedConfig.config.programs.git.extraConfig;
  userGitExtraConfig = {
    github.user = "cdenneen";
  };
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
        signing.signByDefault = true;
        signing.key = null;
        ignores = [ ".DS_Store" "Thumbs.db" ];
        extraConfig = gitExtraConfig // userGitExtraConfig;
        includes = [
          {
            condition = "gitdir:~/src/ap";
            contents = {
              user = {
                name = "Christopher Denneen";
                email = "CDenneen@ap.org";
                signingkey = "3834814930B83A30";
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
                signingkey = "BFEB75D960DFAA6B";
              };
              commit.gpgsign = true;
            };
          }
        ];
      };
      gpg = {
        enable = true;
        publicKeys = [

        ];
      };
      zoxide = {
        options = [
          "--cmd cd"
        ];
      };
    };
    home.packages =
      with pkgs;
      lib.optionals config.profiles.gui.enable [
        spotify
        discord
      ];
    catppuccin = {
      flavor = "latte";
      accent = "pink";
    };
    sops.secrets = {
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
    };
  
  };
}
