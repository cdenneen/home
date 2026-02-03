{ pkgs, ... }:

{
  home.packages = with pkgs; [
    # Shell / UX
    atuin
    bat
    fzf
    zoxide
    eza
    direnv
    starship

    # Kubernetes / cloud CLI
    kubectl
    kubernetes-helm
    kubeswitch
  ];

  programs = {
    atuin.enable = true;
    fzf.enable = true;
    zoxide.enable = true;
    direnv.enable = true;
    direnv.nix-direnv.enable = true;
    starship.enable = true;
  };
}
