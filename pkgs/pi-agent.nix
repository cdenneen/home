{
  buildNpmPackage,
  fetchurl,
  gnutar,
  gzip,
  importNpmLock,
  lib,
  makeWrapper,
  nodejs_24,
  runCommand,
}:

(buildNpmPackage.override { nodejs = nodejs_24; }) {
  pname = "pi-agent";
  version = "0.83.0";

  src =
    runCommand "pi-agent-0.83.0-source"
      {
        nativeBuildInputs = [
          gnutar
          gzip
        ];
      }
      ''
        mkdir -p "$out"
        tar -xzf ${
          fetchurl {
            url = "https://registry.npmjs.org/@earendil-works/pi-coding-agent/-/pi-coding-agent-0.83.0.tgz";
            hash = "sha256-cJf+Szh2Ldp+x4AB57kEMMhJ+69xcyW/6BCXROMiVeY=";
          }
        } --strip-components=1 -C "$out"
        rm -f "$out/npm-shrinkwrap.json" "$out/package.json"
        cp ${./pi-agent/package.json} "$out/package.json"
        cp ${./pi-agent/package-lock.json} "$out/package-lock.json"
      '';
  npmDeps = importNpmLock { npmRoot = ./pi-agent; };
  npmConfigHook = importNpmLock.npmConfigHook;
  npmInstallFlags = [ "--ignore-scripts" ];
  dontNpmBuild = true;

  nativeBuildInputs = [ makeWrapper ];

  installPhase = ''
    runHook preInstall

    mkdir -p "$out/lib/pi-agent" "$out/bin"
    cp -R . "$out/lib/pi-agent"
    makeWrapper ${nodejs_24}/bin/node "$out/bin/pi" \
      --add-flags "$out/lib/pi-agent/dist/cli.js" \
      --set PI_PACKAGE_DIR "$out/lib/pi-agent"

    runHook postInstall
  '';

  doInstallCheck = true;
  installCheckPhase = ''
    runHook preInstallCheck
    "$out/bin/pi" --version | grep -F "0.83.0"
    runHook postInstallCheck
  '';

  meta = {
    description = "Pi coding agent CLI";
    homepage = "https://github.com/earendil-works/pi";
    license = lib.licenses.mit;
    mainProgram = "pi";
    platforms = lib.platforms.linux ++ lib.platforms.darwin;
  };
}
