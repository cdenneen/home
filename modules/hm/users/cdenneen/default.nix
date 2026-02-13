{ ... }:

{
  imports = [
    ./programs.nix
    ./files.nix
    ./session.nix
    ./direnv.nix
    ./git.nix
    ./gpg.nix
    ./zsh.nix
    ./secrets.nix
    ./opencode-telegram-bridge.nix
  ];
}
