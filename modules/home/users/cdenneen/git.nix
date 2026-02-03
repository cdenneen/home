{ lib, ... }:

{
  # Git identity and conditional configuration for cdenneen
  programs.git = {
    enable = true;

    settings.user = {
      name = "Chris Denneen";
      email = "cdenneen@gmail.com";
    };
    signing = {
      key = "0xBFEB75D960DFAA6B";
      signByDefault = true;
    };

    settings.gpg = {
      format = lib.mkForce "openpgp";
      program = "gpg";
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
}
