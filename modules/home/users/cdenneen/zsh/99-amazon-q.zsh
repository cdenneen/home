#█▓▒░ secrets refresh (late)
maybe_refresh_secrets --quiet

#█▓▒░ Amazon Q post block (late)
[[ -f "$HOME/Library/Application Support/amazon-q/shell/zshrc.post.zsh" ]] && builtin source "$HOME/Library/Application Support/amazon-q/shell/zshrc.post.zsh"
