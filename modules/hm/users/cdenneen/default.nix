{ ... }:

{
  # Consolidated Home Manager configuration for user cdenneen.
  # This file serves as the single import point for all user-scoped modules.
  imports = [
    ./programs.nix
    ./files.nix
    ./session.nix
    ./direnv.nix
    ./git.nix
    ./gpg.nix
    ./zsh.nix
    ./secrets.nix
  ];
}
