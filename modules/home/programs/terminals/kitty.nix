{
  config,
  lib,
  pkgs,
  ...
}:
let
  cfg = config.programs.kitty;
in
{
  config = lib.mkIf cfg.enable {
    catppuccin.kitty = {
      enable = true;
      flavor = config.catppuccin.flavor;
    };
    programs.kitty = {
      settings = {
        shell = "${lib.getExe pkgs.ion}";
      };
      font = {
        name = "Fira Code";
        package = pkgs.fira-code;
        size = 12.0;
      };
      keybindings = {
        "super+v" = "paste_from_clipboard";
        "super+c" = "copy_or_interrupt";
        "super+k" = "combine : clear_terminal scroll active : send_text normal,application \x0c";
        "super+equal" = "increase_font_size";
        "super+minus" = "decrease_font_size";
        "super+0" = "restore_font_size";
        "super+shift+g" = "show_last_command_output";
        "super+ctrl+p" = "scroll_to_prompt -1";
        "super+ctrl+n" = "scroll_to_prompt 1";
      };
    };
  };
}
