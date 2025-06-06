{
  config,
  lib,
  pkgs,
  ...
}:
let
  cfg = config.programs.gpg;
  pinPackage = if pkgs.stdenv.isDarwin then pkgs.pinentry_mac else pkgs.pinentry-curses;
in
{
  config = lib.mkIf cfg.enable {
    home.packages = with pkgs; [
      pinPackage
    ];
    programs.zsh.initExtra = ''
      ${pkgs.gnupg}/bin/gpgconf --launch gpg-agent
    '';
    services.gpg-agent = lib.mkIf pkgs.stdenv.isLinux {
      enable = true;
      enableZshIntegration = config.programs.zsh.enable;
      enableFishIntegration = config.programs.fish.enable;
      enableBashIntegration = config.programs.bash.enable;
      enableSshSupport = true;
      defaultCacheTtl = 31536000;
      maxCacheTtl = 31536000;

      extraConfig = ''
        pinentry-program ${pinPackage}/bin/pinentry
      '';
    };
    programs = {
      keychain = {
        enable = false;
        agents = [
          "gpg"
        ];
        extraFlags = [
          "--dir $XDG_DATA_HOME/keychain"
          "--absolute"
          "--quiet"
        ];
      };
      gpg = {
        homedir = "${config.home.homeDirectory}/.gnupg";
        scdaemonSettings = {
          disable-ccid = true; # disable gnupg's built-in smartcard reader functionality
        };
        settings = {
          #█▓▒░ interface
          no-greeting = true;
          use-agent = true;
          list-options = "show-uid-validity";
          verify-options = "show-uid-validity";
          keyid-format = "0xlong";
          keyserver = "hkp://keys.gnupg.net";
          fixed-list-mode = true;
          charset = "utf-8";
          with-fingerprint = true;
          require-cross-certification = true;
          no-emit-version = true;
          no-comments = true;

          #█▓▒░ algos
          personal-digest-preferences = "SHA512 SHA384 SHA224";
          default-preference-list = "SHA512 SHA384 SHA256 SHA224 AES256 AES192 AES CAST5 ZLIB BZIP2 ZIP Uncompressed";
          personal-cipher-preferences = "AES256 AES192 AES CAST5";
          s2k-cipher-algo = "AES256";
          s2k-digest-algo = "SHA512";
          cert-digest-algo = "SHA512";
        };
      };
    };
    home.sessionVariables = {
      GPG_TTY = "$(tty)";
    };
  };
}
