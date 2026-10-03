{
  buildNpmPackage,
  fetchurl,
  gnutar,
  gnugrep,
  gzip,
  importNpmLock,
  lib,
  makeWrapper,
  nodejs_24,
  runCommand,
}:

(buildNpmPackage.override { nodejs = nodejs_24; }) {
  pname = "omniroute";
  version = "3.8.51";

  src =
    runCommand "omniroute-3.8.51-source"
      {
        nativeBuildInputs = [
          gnutar
          gzip
        ];
      }
      ''
        set -euo pipefail
        mkdir -p "$out"
        tar -xzf ${
          fetchurl {
            url = "https://registry.npmjs.org/omniroute/-/omniroute-3.8.51.tgz";
            hash = "sha256-QhaZ/do0vYW88DUlfojjWe3AM8AbhdVLakkhwc2k0u8=";
          }
        } --strip-components=1 -C "$out"
        rm -f "$out/npm-shrinkwrap.json" "$out/package.json"
        cp ${./omniroute/package.json} "$out/package.json"
        cp ${./omniroute/package-lock.json} "$out/package-lock.json"
      '';
  npmDeps = importNpmLock { npmRoot = ./omniroute; };
  npmConfigHook = importNpmLock.npmConfigHook;
  npmInstallFlags = [
    "--ignore-scripts"
    "--legacy-peer-deps"
  ];
  npmRebuildFlags = [ "--ignore-scripts" ];
  dontNpmBuild = true;

  nativeBuildInputs = [ makeWrapper ];

  installPhase = ''
    set -euo pipefail
    runHook preInstall
    mkdir -p "$out/lib/omniroute" "$out/bin"
    cp -R . "$out/lib/omniroute"
    makeWrapper ${nodejs_24}/bin/node "$out/bin/omniroute" \
      --add-flags "$out/lib/omniroute/bin/omniroute.mjs"
    runHook postInstall
  '';

  doInstallCheck = true;
  installCheckPhase = ''
    set -euo pipefail
    "$out/bin/omniroute" --version | ${gnugrep}/bin/grep -F "3.8.51"
  '';

  meta = {
    description = "Local-first AI gateway and model router";
    homepage = "https://omniroute.online";
    license = lib.licenses.mit;
    mainProgram = "omniroute";
    platforms = lib.platforms.linux;
  };
}
