{
  config,
  lib,
  pkgs,
  ...
}:
let
  cfg = config.programs.git;
in
{
  config = lib.mkIf cfg.enable {
    catppuccin = {
      delta = {
        enable = true;
        flavor = config.catppuccin.flavor;
      };
      lazygit = {
        enable = true;
        flavor = config.catppuccin.flavor;
        accent = config.catppuccin.accent;
      };
    };
    programs = {
      delta = {
        enable = true;
        enableGitIntegration = true;
      };
      git = {
        lfs.enable = true;
        signing = {
          signByDefault = true;
          key = null;
          signer = "${pkgs.gnupg}/bin/gpg";
        };
        settings = {
          branch.autosetuprebase = "always";
          color.ui = "auto";
          pull.rebase = "true";
          push.default = "tracking";
          push.autoSetupRemote = true;
          rebase.autostash = "true";
          core.askpass = ""; # needs to be empty to use terminal for ask pass
          core.editor = "nvim";
          core.eol = "lf";
          core.autocrlf = "input";
          # gpg.format = "ssh";
          init.defaultBranch = "main";
          url."git@github.com:".pushInsteadOf = "https://github.com/";
          url."git@gitlab.com:".pushInsteadOf = "https://gitlab.com/";
          alias = {
            a = "add";
            aa = "add -A";
            b = "branch";
            ba = "branch -a";
            c = "commit -m";
            ca = "commit -am";
            cam = "commit --amend --date=now";
            co = "checkout";
            cob = "checkout -b";
            sw = "switch";
            swc = "switch -c";
            s = "status -sb";
            po = "!git push -u origin $(git branch --show-current)";
            d = "diff";
            dc = "diff --cached";
            ignore = "update-index --assume-unchanged";
            unignore = "update-index --no-assume-unchanged";
            ignored = "!git ls-files -v | grep ^h | cut -c 3-";
            rbm = "!git fetch && git rebase origin/main";
            rbc = "-c core.editor=true rebase --continue";
          };
        };
      };
      lazygit.enable = true;
    };
  };
}
