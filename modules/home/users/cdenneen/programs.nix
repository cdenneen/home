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
    tmux

    # Kubernetes / cloud CLI
    kubectl
    kubernetes-helm
    kubeswitch
    fluxcd
    pkgs."fluxcd-operator"
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

  programs.ssh = {
    enable = true;
    matchBlocks."github.com" = {
      user = "git";
      identityFile = "~/.ssh/github_ed25519";
      identitiesOnly = true;
    };
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

  home.file.".config/direnv/lib/k8s_context.bash".text = ''
    #!/usr/bin/env bash

    k8s_context() {
      local path="$1"

      if [ -f "$path/.context" ]; then
        source "$path"/.context
      fi
      if [ ! "$CONTEXT" ]; then
        # Split the path into an array using '/' as the delimiter
        IFS='/' read -ra array <<< "$path"

        # Get the length of the array
        local length=''${#array[@]}

        # Calculate the starting index to get the last three elements
        local start_index=$((length - 3))

        # Ensure start_index is not negative
        start_index=$((start_index >= 0 ? start_index : 0))

        # Extract the last three elements from the array
        last_three_elements=("''${array[@]:start_index}")

        case "''${last_three_elements[0]}" in
          capdev)
            ACCOUNT=dev
            ;;
          awsqa)
            ACCOUNT=qa
            ;;
          apss)
            ACCOUNT=apss
            ;;
          awsprod)
            ACCOUNT=prd
            ;;
        esac
        echo "$ACCOUNT"

        case "''${last_three_elements[1]}" in
          us-east-1)
            REGION=use1
            ;;
          us-west-2)
            REGION=usw2
            ;;
        esac
        echo "$REGION"
        CLUSTER_ID="''${last_three_elements[2]}"
        echo "$CLUSTER_ID"

        CONTEXT="eks_$ACCOUNT-$REGION-$CLUSTER_ID"
      fi
      echo "$CONTEXT"
      # export KUBECONFIG=$(switcher "$CONTEXT")
      KUBECONFIG=$(switcher "$CONTEXT" | sed 's/^__ //' | cut -d, -f1)
      export KUBECONFIG
    }
  '';
}
