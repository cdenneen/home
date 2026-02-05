{ pkgs, ... }:

{
  home.packages = with pkgs; [
    # Shell / UX
    atuin
    bat
    ripgrep
    fzf
    zoxide
    eza
    direnv
    starship

    # Kubernetes / cloud CLI
    kubectl
    kubernetes-helm
    kubeswitch
    glab

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
    # glab config is managed explicitly via home.file
  };

  home.file.".config/glab-cli/config.yml".text = ''
    git_protocol: ssh
    editor:
    browser:
    glamour_style: dark
    check_update: true
    display_hyperlinks: false
    host: git.ap.org
    no_prompt: false
    telemetry: false

    hosts:
      gitlab.com:
        api_protocol: https
        git_protocol: ssh
        user: cdenneen
        container_registry_domains:
          - gitlab.com
          - gitlab.com:443
          - registry.gitlab.com

      git.ap.org:
        api_protocol: https
        git_protocol: ssh
        user: cdenneen
        container_registry_domains:
          - git.ap.org
          - git.ap.org:443
          - registry.associatedpress.com
  '';
}
