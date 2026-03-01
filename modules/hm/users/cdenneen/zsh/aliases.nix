{
  # General
  c = "clear";
  e = "$EDITOR";
  se = "sudoedit";
  vi = "nvim";
  vim = "nvim";
  python = "python3";

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
  kinteract = "switch eks_apinteractives-datateam";
  kinteractdr = "switch eks_apinteractives-datateam-dr";

  # AWS SSO
  sso = "aws sso login --profile sso-apss --no-browser --use-device-code";
  ssod = "aws sso login --profile sso-capdev --no-browser --use-device-code";
  ssoq = "aws sso login --profile sso-awsqa --no-browser --use-device-code";
  ssop = "aws sso login --profile sso-awsprod --no-browser --use-device-code";

  # Tailscale
  tsup = "tailscale up --accept-dns=false";
  tsdown = "tailscale down";
}
