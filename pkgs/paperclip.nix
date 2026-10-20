{
  buildNpmPackage,
  fetchurl,
  gnutar,
  gzip,
  lib,
  makeWrapper,
  nodejs_24,
  runCommand,
}:

(buildNpmPackage.override { nodejs = nodejs_24; }) {
  pname = "paperclip";
  version = "2026.1012.0";

  src =
    runCommand "paperclipai-2026.1012.0-source"
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
            url = "https://registry.npmjs.org/paperclipai/-/paperclipai-2026.1012.0.tgz";
            hash = "sha256-zCMxBYvZPLbU7hjNKbz2PAYh/E9u8MfPeMUkPL6cUK4=";
          }
        } --strip-components=1 -C "$out"
      '';

  # Pre-built tarball — no npm deps to fetch or build steps to run.
  npmDeps = null;
  dontNpmBuild = true;
  npmInstallFlags = [ "--ignore-scripts" ];

  nativeBuildInputs = [ makeWrapper ];

  installPhase = ''
    runHook preInstall

    mkdir -p "$out/lib/paperclip" "$out/bin"
    cp -R . "$out/lib/paperclip"

    makeWrapper ${nodejs_24}/bin/node "$out/bin/paperclipai" \
      --add-flags "$out/lib/paperclip/dist/index.js"

    runHook postInstall
  '';

  meta = {
    description = "Paperclip — orchestrate AI agent teams";
    homepage = "https://github.com/paperclipai/paperclip";
    license = lib.licenses.mit;
    mainProgram = "paperclipai";
    platforms = [
      "x86_64-linux"
      "aarch64-linux"
      "x86_64-darwin"
      "aarch64-darwin"
    ];
  };
}
