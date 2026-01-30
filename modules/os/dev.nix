{
  config,
  lib,
  pkgs,
  ...
}:
let
  cfg = config.profiles;
in
{
  options = {
    profiles.dev.enable = lib.mkEnableOption "Development Programs to be available outside of a devshell";
    environment.pythonPackages = lib.mkOption {
      type = lib.types.listOf lib.types.package;
      default = [ ];
    };
  };

  config = lib.mkIf cfg.dev.enable (
    let
      pythonPackage = pkgs.python312.withPackages (
        ps:
        with ps;
        [
          # pip
          # pipx
          # python-dotenv
          # virtualenv
        ]
        ++ config.environment.pythonPackages
      );
    in
    {
      virtualisation.podman.enable = true;
      virtualisation.podman.dockerCompat = true;

      environment.systemPackages =
        with pkgs;
        [
          cmake
          pythonPackage
          # dfu-util
          gnumake
          # libffi
          # libiconv
          # libusb1
          nodejs
          nodePackages.prettier
          rustup
          # systemfd
          # esp-idf-full
          # zed-editor
        ]
        ++ lib.optionals cfg.gui.enable [
          jetbrains-toolbox
          zed-editor
        ]
        ++ lib.optionals stdenv.isLinux [
          gcc
          clang
        ]
        ++
          lib.optionals
            (builtins.elem pkgs.stdenv.hostPlatform.system [
              "aarch64-darwin"
              "aarch64-linux"
              "x86_64-linux"
            ])
            [
              deno
              neovide
            ];
    }
  );
}
