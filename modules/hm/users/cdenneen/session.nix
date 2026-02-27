{ config, lib, ... }:

{
  # Session-wide defaults for cdenneen
  home.sessionPath = [
    "/etc/profiles/per-user/cdenneen/bin"
    "$HOME/.nix-profile/bin"
  ];
  home.sessionVariables = {
    EDITOR = "nvim";
    VISUAL = "nvim";
    MANPAGER = lib.mkForce "nvim --cmd ':lua vim.g.noplugins=1' +Man!";
    MANWIDTH = "999";
    # Keep XDG defaults explicit for tools that don't set them.
    XDG_DATA_HOME = "${config.home.homeDirectory}/.local/share";
    XDG_CACHE_HOME = "${config.home.homeDirectory}/.cache";
    AWS_SHARED_CREDENTIALS_FILE = "$HOME/.aws/credentials";
    AWS_CONFIG_FILE = "$HOME/.aws/config";
    KUBECACHEDIR = "$XDG_RUNTIME_DIR/kube";
    # Avoid relying on ordering for XDG_CACHE_HOME expansion.
    STARSHIP_CACHE = "$HOME/.cache/starship";
    TFENV = "$XDG_DATA_HOME/terraform";
    CM_LAUNCHER = "fzf";
    FZF_DEFAULT_OPTS = lib.mkForce ''
      --color=fg:#ccd2d9,fg+:#d0d0d0,bg:#39274D,bg+:#39274D
      --color=hl:#875FAF,hl+:#87FF5F,info:#ab92fc,marker:#87FF5F
      --color=prompt:#87FF5F,spinner:#87FF5F,pointer:#87FF5F,header:#483160
      --color=gutter:#483160,border:#39274D,preview-fg:#e1d6f8,preview-bg:#201430
      --color=preview-border:#875FAF,preview-scrollbar:#875FAF,preview-label:#87FF5F,label:#8edf5f
      --color=query:#d9d9d9,disabled:#3f3d46
      --border=block --border-label-pos=0 --preview-window=border-bold
      --padding=0 --margin=1 --prompt=❯  --marker=❯
      --pointer=◈ --separator=~ --scrollbar=▌ --layout=reverse
    '';
    LC_COLLATE = "en_US.UTF-8";
    LC_CTYPE = "en_US.UTF-8";
    LC_MESSAGES = "en_US.UTF-8";
    LC_MONETARY = "en_US.UTF-8";
    LC_NUMERIC = "en_US.UTF-8";
    LC_TIME = "en_US.UTF-8";
    LC_ALL = "en_US.UTF-8";
    LANG = "en_US.UTF-8";
    LANGUAGE = "en_US.UTF-8";
    LESSCHARSET = "utf-8";
    MOSH_TITLE_NOPREFIX = "1";
  };
}
