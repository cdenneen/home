{ config, lib, pkgs, ... }:
let
  cfg = config.programs.awscli;
in
{
  config = lib.mkIf cfg.enable {
    programs.awscli = {
      package = pkgs.awscli2;
    };
  };
}
