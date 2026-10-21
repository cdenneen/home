{
  buildNpmPackage,
  fetchurl,
  importNpmLock,
  lib,
  makeWrapper,
  nodejs_24,
}:

(buildNpmPackage.override { nodejs = nodejs_24; }) {
  pname = "paperclip";
  version = "2026.1012.0";

  # Fetch the pre-built tarball — dist/index.js is already compiled.
  # We only need npm to install the 13 runtime dependencies (zod, ws, etc).
  src = fetchurl {
    url = "https://registry.npmjs.org/paperclipai/-/paperclipai-2026.1012.0.tgz";
    hash = "sha256-zCMxBYvZPLbU7hjNKbz2PAYh/E9u8MfPeMUkPL6cUK4=";
  };

  # package-lock.json generated from the tarball's package.json.
  # Provides the dependency tree for importNpmLock.
  npmDeps = importNpmLock { npmRoot = ./paperclip; };
  npmConfigHook = importNpmLock.npmConfigHook;

  # dist/ is pre-built — skip the build step, only install deps.
  dontNpmBuild = true;
  npmInstallFlags = [
    "--ignore-scripts"
    "--omit=dev"
  ];

  nativeBuildInputs = [ makeWrapper ];

  installPhase = ''
    runHook preInstall

    mkdir -p "$out/lib/paperclip" "$out/bin"
    cp -R . "$out/lib/paperclip/"

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
