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

    # Runtimes / cloud
    nodejs_20 # LTS
    yarn
    awscli2
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
