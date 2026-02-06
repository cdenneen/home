{ pkgs, lib, ... }:

{
  programs.zsh.enable = true;

  # Ensure correct PATH setup for zsh across platforms.
  programs.zsh.envExtra = lib.concatStringsSep "\n" [
    (lib.optionalString pkgs.stdenv.isDarwin ''
      export PATH="/run/current-system/sw/bin:$HOME/.nix-profile/bin:$PATH"
      if [ -r /etc/zprofile ]; then
        source /etc/zprofile
      fi
    '')
    (lib.optionalString pkgs.stdenv.isLinux ''
      if [ -r /etc/profile ]; then
        source /etc/profile
      fi
    '')
  ];

  programs.zsh.shellAliases = {
    # General
    c = "clear";
    e = "$EDITOR";
    se = "sudoedit";
    vi = "nvim";
    vim = "nvim";

    # Git
    g = "git";
    ga = "git add";
    gb = "git branch";
    gc = "git commit";
    gcm = "git commit -m";
    gco = "git checkout";
    gcob = "git checkout -b";
    gcp = "git cherry-pick";
    gd = "git diff";
    gdiff = "git diff";
    gf = "git fetch";
    gl = "git prettylog";
    gm = "git merge";
    gp = "git push";
    gpl = "git pull";
    gpr = "git pull --rebase";
    gr = "git rebase -i";
    gs = "git status -sb";
    gt = "git tag";
    gu = "git reset @ --";
    gx = "git reset --hard @";

    # Jujutsu
    jf = "jj git fetch";
    jn = "jj new";
    js = "jj st";

    # Kubernetes
    k = "kubectl";
    kprod = "switch eks_prod-2-use1";
    kshared = "switch eks_shared-1-use1";
    kinteract = "switch eks_eks-prod-us-east-1-apinteractives-datateam/eks_apinteractives-datateam";
    kinteractdr = "switch eks_eks-prod-us-west-2-apinteractives-datateam-dr/eks_apinteractives-datateam-dr";

    # AWS SSO
    sso = "aws sso login --profile sso-apss --no-browser --use-device-code";
    ssod = "aws sso login --profile sso-capdev --no-browser --use-device-code";
    ssoq = "aws sso login --profile sso-awsqa --no-browser --use-device-code";
    ssop = "aws sso login --profile sso-awsprod --no-browser --use-device-code";
  };

  # Ensure gpg-agent/pinentry works across sessions/TTYs.
  programs.zsh.initContent = lib.mkAfter ''
    gpgconf --launch gpg-agent >/dev/null 2>&1 || true
  '';
}
