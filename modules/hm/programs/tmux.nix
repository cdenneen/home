{ pkgs, ... }:
{
  programs.tmux = {
    enable = true;
    terminal = "tmux-256color";
    mouse = true;
    historyLimit = 100000;

    extraConfig = ''
      set -g default-terminal "tmux-256color"
      set -as terminal-features ",xterm-256color:RGB"
      set -g status-position top
      set -g base-index 1
      setw -g pane-base-index 1
      set -g renumber-windows on

      set -g status on
      set -g status-interval 5
      set -g status-justify left
      set -g status-left-length 60
      set -g status-right-length 120

      set -g status-left "#[fg=cyan,bold]#H #[fg=white]• #[fg=cyan]#{session_name}"
      set -g status-right "#[fg=white]%Y-%m-%d #[fg=white]%H:%M"

      set -g window-status-format "#[fg=white] #I:#W "
      set -g window-status-current-format "#[fg=green,bold] #I:#W "
      set -g window-status-separator ""

      bind r source-file ~/.config/tmux/tmux.conf \; display-message "tmux reloaded"

      setw -g mode-keys vi
      bind -T copy-mode-vi v send -X begin-selection
      bind -T copy-mode-vi y send -X copy-selection

      bind -r H resize-pane -L 5
      bind -r J resize-pane -D 5
      bind -r K resize-pane -U 5
      bind -r L resize-pane -R 5

      bind b set-option -g status
      bind s set-window-option synchronize-panes
    '';

    plugins = with pkgs.tmuxPlugins; [
      sensible
      yank
      resurrect
      continuum
      vim-tmux-navigator
    ];
  };
}
