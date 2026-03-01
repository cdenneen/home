{
  config,
  pkgs,
  lib,
  ...
}:

let
  initParts = [
    (import ./zsh/init/core.nix)
    (import ./zsh/init/completion.nix)
    (import ./zsh/init/keybindings.nix)
    (import ./zsh/init/less.nix)
    (import ./zsh/init/clipboard.nix)
    (import ./zsh/init/functions.nix)
  ];
in
{
  programs.zsh.enable = true;
  programs.zsh.enableCompletion = true;

  programs.zsh.syntaxHighlighting.styles = {
    default = "none";
    cursor = "bg=10";
    "unknown-token" = "fg=9,bold";
    "reserved-word" = "fg=3";
    alias = "fg=4";
    builtin = "fg=4";
    function = "fg=4";
    command = "fg=4";
    precommand = "none";
    commandseparator = "none";
    "hashed-command" = "fg=12";
    path = "none";
    path_prefix = "none";
    path_approx = "fg=3";
    globbing = "fg=10";
    "history-expansion" = "fg=10";
    "single-hyphen-option" = "fg=12";
    "double-hyphen-option" = "fg=13";
    "back-quoted-argument" = "none";
    "single-quoted-argument" = "fg=3";
    "double-quoted-argument" = "fg=3";
    "dollar-double-quoted-argument" = "fg=6";
    "back-double-quoted-argument" = "fg=6";
    assign = "none";
  };

  programs.zsh.envExtra = import ./zsh/env.nix { inherit pkgs lib; };

  programs.zsh.shellAliases = import ./zsh/aliases.nix;

  # Ensure git aliases and helper functions are available.
  programs.zsh.initContent = lib.mkAfter (lib.concatStringsSep "\n\n" initParts);
}
