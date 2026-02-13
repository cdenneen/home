{
  config,
  lib,
  pkgs,
  ...
}:

let
  # GPG secret keys stored in SOPS (armored). Add new keys here as needed.
  gpgSecretNames = [
    "gpg_gmail"
    "gpg_ap"
  ];
in
{
  sops.secrets = lib.genAttrs gpgSecretNames (name: {
    sopsFile = ../../../../secrets/secrets.yaml;
    key = name;
  });

  programs.gpg = {
    enable = true;
    publicKeys = [
      {
        source = ../../../../pub/personal.pub;
        trust = 5;
      }
      {
        source = ../../../../pub/work.pub;
        trust = 5;
      }
    ];
  };

  services.gpg-agent = {
    enable = true;

    # Use gpg-agent as an ssh-agent replacement.
    enableSshSupport = true;

    # Cache the key once per boot/login session (no repeated prompts).
    defaultCacheTtl = 86400;
    maxCacheTtl = 86400;

    pinentry.package = if pkgs.stdenv.isDarwin then pkgs.pinentry_mac else pkgs.pinentry-curses;
  };

  # Import all decrypted GPG secret keys from SOPS exactly once.
  home.activation.importGpgSecrets = lib.hm.dag.entryAfter [ "writeBoundary" ] (
    lib.concatStringsSep "\n" (
      map (name: ''
        if [ -f "${config.sops.secrets.${name}.path}" ]; then
          ${config.programs.gpg.package}/bin/gpg --batch --import "${
            config.sops.secrets.${name}.path
          }" >/dev/null 2>&1 || true
        fi
      '') gpgSecretNames
    )
  );
}
