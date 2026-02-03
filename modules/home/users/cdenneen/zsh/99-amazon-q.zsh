#█▓▒░ secrets
# Source secrets if present; do not refresh automatically
[ -f "$HOME/.secrets" ] && source "$HOME/.secrets"

#█▓▒░ Amazon Q post block (late)
[[ -f "$HOME/Library/Application Support/amazon-q/shell/zshrc.post.zsh" ]] && builtin source "$HOME/Library/Application Support/amazon-q/shell/zshrc.post.zsh"
