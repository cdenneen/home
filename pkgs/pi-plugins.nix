{
  buildNpmPackage,
  importNpmLock,
  lib,
  nodejs_24,
}:

(buildNpmPackage.override { nodejs = nodejs_24; }) {
  pname = "pi-plugins";
  version = "1.0.0";

  src = ./pi-plugins;
  npmDeps = importNpmLock { npmRoot = ./pi-plugins; };
  npmConfigHook = importNpmLock.npmConfigHook;
  npmFlags = [ "--legacy-peer-deps" ];
  npmInstallFlags = [ "--ignore-scripts" ];
  dontNpmBuild = true;

  preInstall = ''
    pushd node_modules/better-sqlite3
    unset npm_config_nodedir
    ${nodejs_24}/bin/node \
      ${nodejs_24}/lib/node_modules/npm/node_modules/node-gyp/bin/node-gyp.js \
      rebuild --release --nodedir=${nodejs_24}
    popd
  '';

  installPhase = ''
    runHook preInstall

    mkdir -p "$out/lib/pi-plugins"
    cp -R node_modules "$out/lib/pi-plugins/"

    runHook postInstall
  '';

  doInstallCheck = true;
  installCheckPhase = ''
    runHook preInstallCheck
    test -f "$out/lib/pi-plugins/node_modules/pi-mcp-adapter/package.json"
    test -f "$out/lib/pi-plugins/node_modules/pi-subagents/package.json"
    test -f "$out/lib/pi-plugins/node_modules/pi-hermes-memory/package.json"
    ${nodejs_24}/bin/node -e '
      const Database = require(process.argv[1]);
      const db = new Database(":memory:");
      if (db.prepare("select 1 as ok").get().ok !== 1) process.exit(1);
      db.close();
    ' "$out/lib/pi-plugins/node_modules/better-sqlite3"
    runHook postInstallCheck
  '';

  meta = {
    description = "Pinned Pi extension bundle for cdenneen";
    license = lib.licenses.mit;
    platforms = lib.platforms.linux ++ lib.platforms.darwin;
  };
}
