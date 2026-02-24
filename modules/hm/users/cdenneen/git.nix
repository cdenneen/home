{
  config,
  lib,
  ...
}:

{
  # Git identity and conditional configuration for cdenneen
  programs.git = {
    enable = true;

    settings.user = {
      name = "Chris Denneen";
      email = "cdenneen@gmail.com";
    };
    settings.gpg.ssh.allowedSignersFile = "~/.config/git/allowed_signers";
    signing = {
      key = config.sops.secrets.github_ed25519.path;
      signByDefault = true;
    };

    ignores = [
      ".DS_Store"
      "Thumbs.db"
    ];

    includes = [
      {
        condition = "hasconfig:remote.*.url:git@git.ap.org:*/**";
        contents = {
          user = {
            name = "Christopher Denneen";
            email = "cdenneen@ap.org";
            signingkey = config.sops.secrets.cdenneen_ed25519_2024.path;
          };
          gpg.format = "ssh";
          commit.gpgsign = true;
        };
      }
      {
        condition = "hasconfig:remote.*.url:ssh://git@git.ap.org/**/**";
        contents = {
          user = {
            name = "Christopher Denneen";
            email = "cdenneen@ap.org";
            signingkey = config.sops.secrets.cdenneen_ed25519_2024.path;
          };
          gpg.format = "ssh";
          commit.gpgsign = true;
        };
      }
      {
        condition = "hasconfig:remote.*.url:https://git.ap.org/**/**";
        contents = {
          user = {
            name = "Christopher Denneen";
            email = "cdenneen@ap.org";
            signingkey = config.sops.secrets.cdenneen_ed25519_2024.path;
          };
          gpg.format = "ssh";
          commit.gpgsign = true;
        };
      }
    ];
  };

  home.file.".config/git/allowed_signers".text = ''
    cdenneen@gmail.com ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIO+Ahsv5qSvj2FuIIJxBuqSancb4Oi6Kf7xLo99dIaRL
    cdenneen@ap.org ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAII1avpzyzr4rhp/LyD9JrcO+DJP+6pBMwbOglSBXHudF
  '';
}
