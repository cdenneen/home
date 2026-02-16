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
        condition = "gitdir:~/src/ap";
        contents = {
          user = {
            name = "Christopher Denneen";
            email = "CDenneen@ap.org";
            signingkey = "~/.ssh/id_ed25519";
          };
          gpg.format = "ssh";
          commit.gpgsign = true;
        };
      }
      {
        condition = "hasconfig:remote.*.url:git.ap.org";
        contents = {
          user = {
            name = "Christopher Denneen";
            email = "CDenneen@ap.org";
            signingkey = "~/.ssh/id_ed25519";
          };
          gpg.format = "ssh";
          commit.gpgsign = true;
        };
      }
      {
        condition = "gitdir:~/src/personal";
        contents = {
          user = {
            name = "Chris Denneen";
            email = "cdenneen@gmail.com";
            signingkey = config.sops.secrets.github_ed25519.path;
          };
          gpg.format = "ssh";
          commit.gpgsign = true;
        };
      }
    ];
  };
}
