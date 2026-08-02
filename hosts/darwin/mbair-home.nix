{
  config,
  lib,
  pkgs,
  ...
}:
{
  profiles.gui.enable = lib.mkForce false;

  catppuccin = {
    enable = lib.mkForce false;
    bat.enable = lib.mkForce false;
    fzf.enable = lib.mkForce false;
    starship.enable = lib.mkForce false;
    tmux.enable = lib.mkForce false;
  };

  home.file.".zshenv".text = lib.mkForce ''
    # Environment variables
    if [ -r "${config.home.sessionVariablesPackage}/etc/profile.d/hm-session-vars.sh" ]; then
      . "${config.home.sessionVariablesPackage}/etc/profile.d/hm-session-vars.sh"
    elif [ -r "${config.home.profileDirectory}/etc/profile.d/hm-session-vars.sh" ]; then
      . "${config.home.profileDirectory}/etc/profile.d/hm-session-vars.sh"
    fi

    ${config.programs.zsh.envExtra}
  '';

  home.packages = lib.mkForce (
    with pkgs;
    [
      _1password-cli
      age
      atuin
      bash
      bat
      cargo
      coreutils
      direnv
      eza
      fzf
      git
      git-lfs
      jq
      lazygit
      ripgrep
      sops
      starship
      tmux
      zoxide
    ]
  );

  programs = {
    alacritty.enable = lib.mkForce false;
    awscli.enable = lib.mkForce false;
    git.extraConfig.core.editor = lib.mkForce "vi";
    helix.enable = lib.mkForce false;
    kitty.enable = lib.mkForce false;
    nh.enable = lib.mkForce false;
    nvim.enable = lib.mkForce false;
    rio.enable = lib.mkForce false;
    wezterm.enable = lib.mkForce false;
    zellij.enable = lib.mkForce false;
    zsh.shellAliases = {
      vi = lib.mkOverride 40 "/usr/bin/vi";
      vim = lib.mkOverride 40 "/usr/bin/vi";
    };
  };

  services.syncthing.enable = lib.mkForce false;

  launchd.agents = {
    lemonade.enable = lib.mkForce false;
    opencode-serve.enable = lib.mkForce false;
    peps-service.enable = lib.mkForce false;
  };

  home.sessionVariables = {
    EDITOR = lib.mkOverride 40 "/usr/bin/vi";
    VISUAL = lib.mkOverride 40 "/usr/bin/vi";
    MANPAGER = lib.mkOverride 40 "less -R";
    SOPS_AGE_KEY_FILE = "$HOME/Library/Application Support/sops/age/keys.txt";
  };
}
