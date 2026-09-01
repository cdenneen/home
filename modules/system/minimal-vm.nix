{ config, lib, pkgs, homeStateVersion ? "26.05", ... }:
{
  options.profiles.minimalVm.enable = lib.mkEnableOption "Minimal cloud VM profile (zsh, nvim, tailscale, no theme/shell bloat)";

  config = lib.mkIf config.profiles.minimalVm.enable {
    # Deliberately skip profiles.defaults -- that pulls in catppuccin,
    # starship, atuin, zoxide, eza, fzf, direnv, etc. Free-tier VMs are RAM-
    # and disk-constrained; this is the bare minimum instead.
    profiles.aiTools.enable = lib.mkForce false;

    # ARCHITECTURE CONSTRAINT (confirmed the hard way, twice): sharedModules
    # (catppuccin, nix-index-database, nur, sops, and self.homeModules.default
    # = ./modules/hm) decorate ANY key present in home-manager.users,
    # completely independent of profiles.hmIntegrated/defaults/aiTools.
    # self.homeModules.default unconditionally imports ./modules/hm/users
    # (-> cdenneen/default.nix: programs.nix, files.nix, session.nix,
    # hyprland.nix, etc. -- syncthing, rtk, codex/opencode configs) and
    # ./modules/hm/programs (bat/tmux/editors/terminals/zellij/...) -- the
    # user's full personal environment, all set unconditionally. (GTK/KDE/
    # Hyprland specifically are NOT part of this -- gtk.nix, kde.nix, and
    # hyprland.nix are already gated on profiles.gui.enable, which defaults
    # false and nothing here sets true, so no GUI ever gets pulled in.)
    #
    # Tried to make the "cdenneen" key not exist at all when opting out:
    # `a.b.c = mkIf cond val` still structurally registers the "c" key
    # during module merging regardless of cond's runtime value (attribute
    # NAMES are eager in the module system; only VALUES are lazy under
    # mkIf). Tried `lib.optionalAttrs config.profiles.hmIntegrated.enable
    # {...}` in modules/system/users/cdenneen.nix instead, since
    # optionalAttrs is a plain function and should skip evaluating the key
    # entirely when false -- but referencing `config.*` inside optionalAttrs
    # breaks the module system's fixpoint (infinite recursion); mkIf exists
    # specifically because it's the only construct that can safely depend on
    # `config` without that recursion. So there is no way, in this flake's
    # current architecture, to keep the "cdenneen" home-manager key from
    # existing -- and once it exists, sharedModules apply regardless.
    #
    # Accepting that baseline weight rather than fighting the architecture
    # further. What IS still under this host's control: aiTools (above) and
    # catppuccin/starship (below, since those specifically caused a real
    # IFD-driven build failure, not just closure size). Verify actual disk
    # usage against the 30GB cap once built; if it's genuinely too tight,
    # the fix is a bigger persistent disk (small $, still ~$0 in spirit) or
    # editing modules/hm directly, not more per-host profile flags.
    home-manager.users.${config.userPresets.cdenneen.name} = {
      home.stateVersion = homeStateVersion;
      programs.home-manager.enable = true;
      programs.zsh.enable = true;
      catppuccin.enable = lib.mkForce false;
      catppuccin.starship.enable = lib.mkForce false;
      catppuccin.bat.enable = lib.mkForce false;
      catppuccin.fzf.enable = lib.mkForce false;
      catppuccin.tmux.enable = lib.mkForce false;
      programs.starship.enable = lib.mkForce false;
      # cdenneen/programs.nix sets `services.syncthing.tray.enable =
      # pkgs.stdenv.hostPlatform.isLinux;` unconditionally -- that pulls in syncthingtray
      # (a Qt6 GUI tray icon: qtbase/qtdeclarative/qtsvg/qttools/qtwayland),
      # a genuinely heavy, genuinely unnecessary dependency on a headless VM
      # with no desktop to sync files with in the first place. Unlike the
      # GTK/KDE/Hyprland modules (already gated on profiles.gui.enable),
      # this one isn't behind any profile flag, so force it off directly.
      services.syncthing.enable = lib.mkForce false;
      # programs.opencode (its own home-manager module, not something in
      # this repo) defaults its own enable to true regardless of
      # profiles.aiTools -- same pattern as catppuccin.starship above. This
      # is what pulled opencode-node_modules into the closure even with
      # aiTools off. hosts/nixos/wsl-home.nix and
      # hosts/nixos/MacBook-Pro-NixOS-home.nix already force this off for
      # the same reason -- matching that existing precedent rather than
      # inventing a new pattern. Keeps pi/hermes/codex, which the user
      # wants, and only cuts opencode specifically.
      programs.opencode.enable = lib.mkForce false;
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

      # profiles.defaults is where the cachix substituter normally comes
      # from, and this profile skips defaults entirely -- so these boxes had
      # zero cache access and were compiling everything from source on every
      # switch. That, not RAM, is why builds were hanging/OOMing (see
      # ci.yml's minimalvm-full-linux job, which populates cdenneen.cachix.org
      # for these exact hosts).
      substituters = lib.mkDefault [
        "https://cache.nixos.org"
        "https://nix-community.cachix.org"
        "https://cdenneen.cachix.org"
      ];
      trusted-public-keys = lib.mkDefault [
        "cache.nixos.org-1:6NCHdD59X431o0gWypbMrAURkbJ16ZPMQFGspcDShjY="
        "nix-community.cachix.org-1:mB9FSh9qf2dCimDSUo8Zy7bkq5CX+/rkCWyvRCYg3Fs="
        "cdenneen.cachix.org-1:EUognwSf1y0FAzDOPmUuYtz6aOxCWyNbcMi8PjHV8gU="
      ];
    };
    boot.tmp.cleanOnBoot = lib.mkDefault true;
  };
}
