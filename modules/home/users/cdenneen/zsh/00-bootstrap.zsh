#█▓▒░ bootstrap (early)

setopt globdots

# Ensure PATH always includes system utilities.
# In devshell/direnv contexts PATH can lose /usr/bin which breaks git and scripts.
typeset -U path PATH
path=(
  /run/current-system/sw/bin
  "$HOME/.nix-profile/bin"
  /usr/local/bin
  /usr/bin
  /bin
  /usr/sbin
  /sbin
  $path
)
export PATH

# Amazon Q pre block. Keep early.
[[ -f "$HOME/Library/Application Support/amazon-q/shell/zshrc.pre.zsh" ]] && builtin source "$HOME/Library/Application Support/amazon-q/shell/zshrc.pre.zsh"

# Some third-party scripts can enable tracing or redirect it (PS4 empty), which
# causes confusing output. Keep the shell quiet by default.
set +x +v 2>/dev/null || true
unsetopt xtrace verbose 2>/dev/null || true
unset XTRACEFD 2>/dev/null || true

# Prefer gpg-agent as ssh-agent if available.
if [[ -z "$SSH_AUTH_SOCK" ]] && command -v gpgconf >/dev/null 2>&1; then
  export SSH_AUTH_SOCK="$(gpgconf --list-dirs agent-ssh-socket)"
fi

# Completion-heavy init lives in 02-autocompletion.zsh (after compinit).
