#█▓▒░ bootstrap (early)

setopt globdots

# Amazon Q pre block. Keep early.
[[ -f "$HOME/Library/Application Support/amazon-q/shell/zshrc.pre.zsh" ]] && builtin source "$HOME/Library/Application Support/amazon-q/shell/zshrc.pre.zsh"

# Prefer gpg-agent as ssh-agent if available.
if [[ -z "$SSH_AUTH_SOCK" ]] && command -v gpgconf >/dev/null 2>&1; then
  export SSH_AUTH_SOCK="$(gpgconf --list-dirs agent-ssh-socket)"
fi

# kubeswitch / switcher integration
command -v switcher >/dev/null 2>&1 && source <(switcher init zsh)
command -v switch >/dev/null 2>&1 && source <(switch completion zsh)
