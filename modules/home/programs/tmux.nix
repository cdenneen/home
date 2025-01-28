{ config, lib, pkgs, ... }:
with pkgs;
let
  cfg = config.programs.tmux;
in
{
  config = lib.mkIf cfg.enable {
    catppuccin.tmux = {
      enable = true;
      flavor = config.catppuccin.flavor;
      extraConfig = ''
        set -g @catppuccin_window_left_separator ""
        set -g @catppuccin_window_right_separator " "
        set -g @catppuccin_window_middle_separator " █"
        set -g @catppuccin_window_number_position "right"
  
        set -g @catppuccin_window_default_fill "number"
        set -g @catppuccin_window_default_text "#W"
  
        set -g @catppuccin_window_current_fill "number"
        set -g @catppuccin_window_current_text "#W"
  
        set -g @catppuccin_status_modules_left "host session"
        set -g @catppuccin_status_modules_right "date_time"
        set -g @catppuccin_status_left_separator  " "
        set -g @catppuccin_status_right_separator ""
        set -g @catppuccin_status_fill "icon"
        set -g @catppuccin_status_connect_separator "no"
  
        set -g @catppuccin_directory_text "#{pane_current_path}"
      '';
    };
    programs.tmux = {
      aggressiveResize = true;
      baseIndex = 1;
      clock24 = false;
      customPaneNavigationAndResize = false;
      escapeTime = 0;
      historyLimit = 10000;
      keyMode = "vi";
      newSession = false;
      resizeAmount = 5;
      reverseSplit = false;
      secureSocket = false;
      sensibleOnTop = false;
      mouse = true;
      plugins = with tmuxPlugins; [
        yank
        vim-tmux-navigator
        {
          plugin = tmux-thumbs;
          extraConfig = ''
            # sticky fingers
            unbind f
            set -g @thumbs-key f
            set -g @thumbs-contrast 1
            set -g @thumbs-bg-color '#b968fc'
            set -g @thumbs-fg-color '#201430'
            set -g @thumbs-hint-bg-color '#87ff5f'
            set -g @thumbs-hint-fg-color '#201430'
            set -g @thumbs-select-bg-color '#9CDA7C'
            set -g @thumbs-select-fg-color '#201430'
            set -g @thumbs-command 'printf "{}" | yank'
          '';
        }
        {
          plugin = mode-indicator;
          extraConfig = ''
            # status icon
            set -g @mode_indicator_empty_prompt ' ◇ '
            set -g @mode_indicator_empty_mode_style 'bg=term,fg=color2'
            set -g @mode_indicator_prefix_prompt ' ◈ '
            set -g @mode_indicator_prefix_mode_style 'bg=color2,fg=color0'
            set -g @mode_indicator_copy_prompt '  '
            set -g @mode_indicator_copy_mode_style 'bg=color10,fg=color0'
            set -g @mode_indicator_sync_prompt '   '
            set -g @mode_indicator_sync_mode_style 'bg=color6,fg=color0'
          '';
        }
        mode-indicator
        {
          plugin = resurrect;
          extraConfig = ''
            set -g @resurrect-capture-pane-contents 'on'
          '';
        }
        {
          plugin = continuum;
          extraConfig = ''
            set -g @continuum-restore 'on'
          '';
        }
        {
          plugin = dracula;
          extraConfig = ''
            set -g @dracula-plugins "kubernetes-context git terraform cwd ram-usage cpu-usge time"
            set -g @dracula-kubernetes-eks-hide-arn true
            set -g @dracula-kubernetes-eks-extra-account true
            set -g @dracula-refresh-rate 10
          '';
        }
        sensible
      ];
    };
  };
}
