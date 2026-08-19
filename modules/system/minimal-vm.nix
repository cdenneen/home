{ config, lib, pkgs, homeStateVersion ? "25.11", ... }:
{
  options.profiles.minimalVm.enable = lib.mkEnableOption "Minimal cloud VM profile (zsh, nvim, tailscale, no theme/shell bloat)";

  config = lib.mkIf config.profiles.minimalVm.enable {
    # Deliberately skip profiles.defaults -- that pulls in catppuccin,
    # starship, atuin, zoxide, eza, fzf, direnv, etc. Free-tier VMs are RAM-
    # and disk-constrained; this is the bare minimum instead.
    home-manager.users.${config.userPresets.cdenneen.name} = {
      home.stateVersion = homeStateVersion;
      programs.home-manager.enable = true;
      programs.zsh.enable = true;
      programs.neovim.enable = true;
      # catppuccin-nix defaults each per-app integration independently
      # upstream -- setting the top-level catppuccin.enable off does NOT
      # cascade to these. catppuccin.starship is what triggered the
      # IFD-driven signature/build failure; bat/fzf/tmux are just bloat.
      catppuccin.enable = lib.mkForce false;
      catppuccin.starship.enable = lib.mkForce false;
      catppuccin.bat.enable = lib.mkForce false;
      catppuccin.fzf.enable = lib.mkForce false;
      catppuccin.tmux.enable = lib.mkForce false;
      # Shared catppuccin module defaults this on via mkDefault regardless;
      # plain zsh prompt is enough here.
      programs.starship.enable = lib.mkForce false;
    };

    # Storage is capped at 30GB (Always Free max) -- ghost had 200GB, so its
    # 3-day GC window and 5-15GB min/max-free were tuned for ~7x more disk.
    # Scaled down here: tighter retention plus size-triggered auto-GC that
    # reacts to actual free-space pressure instead of waiting for the nightly
    # calendar job.
    nix.gc = {
      automatic = true;
      dates = lib.mkDefault "daily";
      options = lib.mkDefault "--delete-older-than 1d";
    };
    nix.settings = {
      min-free = lib.mkDefault (2 * 1024 * 1024 * 1024);
      max-free = lib.mkDefault (5 * 1024 * 1024 * 1024);
    };
    boot.tmp.cleanOnBoot = lib.mkDefault true;
  };
}
