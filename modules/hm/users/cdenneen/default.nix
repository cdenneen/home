{ fluxcdAgentSkills, ... }:

{
  imports = [
    fluxcdAgentSkills.homeManagerModules.default
    ./programs.nix
    ./files.nix
    ./jarvis.nix
    ./session.nix
    ./direnv.nix
    ./git.nix
    ./gpg.nix
    ./hyprland.nix
    ./zsh.nix
    ./secrets.nix
  ];
}
