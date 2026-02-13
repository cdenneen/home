{ config, lib, ... }:
let
  cfg = config.programs.eza;
  aliases = {
    ls = "eza";
    ll = "eza -lgo";
    la = "eza -a";
    lt = "eza -T";
    lla = "eza -lago";
    lta = "eza -Ta";
    llta = "eza -lTago";
  };
in
{
  config = lib.mkIf cfg.enable {
    programs = {
      bash.shellAliases = aliases;
      zsh.shellAliases = aliases;
    };
  };
}
