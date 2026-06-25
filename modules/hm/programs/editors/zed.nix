{
  config,
  lib,
  pkgs,
  ...
}:
{
  options.programs.zed = {
    enable = lib.mkEnableOption "enable zed";
    package = lib.mkOption {
      type = lib.types.package;
      default = pkgs.zed-editor;
    };
  };

  config = lib.mkIf config.programs.zed.enable {
    xdg.configFile."zed/settings.json".source =
      config.lib.file.mkOutOfStoreSymlink "${config.home.homeDirectory}/nixcfg/modules/hm/programs/editors/zed-settings.json";
    home.packages = [ config.programs.zed.package ];
  };
}
