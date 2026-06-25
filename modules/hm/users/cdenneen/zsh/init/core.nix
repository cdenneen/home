''
  # Synthetic worktree branch safety: prefer <branch>@<workspace>.
  # These override the shell aliases of the same name.
  unalias gco gcob >/dev/null 2>&1 || true

  # Keep "gco" / "gcob" muscle-memory paths but delegate to git aliases,
  # so there's only one implementation of the synthetic branch logic.
  gco() { git co "$@"; }
  gcob() { git cob "$@"; }

  # kubeswitch integration (no external zsh snippets).
  command -v switcher >/dev/null 2>&1 && eval "$(switcher init zsh)"
  command -v switch >/dev/null 2>&1 && eval "$(switch completion zsh)"
''
