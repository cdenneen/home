{
  fetchurl,
  lib,
  stdenvNoCC,
}:

let
  version = "0.137.0";
  releaseTag = "rust-v${version}";

  sources = {
    aarch64-darwin = {
      asset = "codex-aarch64-apple-darwin.tar.gz";
      binary = "codex-aarch64-apple-darwin";
      hash = "sha256-Yo0ieLH6KkZ0UmNfL9Wq7umN5KlPKvBgMeR5DaaEQEY=";
    };
    x86_64-darwin = {
      asset = "codex-x86_64-apple-darwin.tar.gz";
      binary = "codex-x86_64-apple-darwin";
      hash = "sha256-MiBv+OTts0IoMrJNJaUhJG5EeLZ7qy7GO+5jL9+UswY=";
    };
    x86_64-linux = {
      asset = "codex-x86_64-unknown-linux-musl.tar.gz";
      binary = "codex-x86_64-unknown-linux-musl";
      hash = "sha256-2W6IMTuVWX6cu4cE9tsW27gcBxQrCM+2KEeatDNpaTE=";
    };
    aarch64-linux = {
      asset = "codex-aarch64-unknown-linux-musl.tar.gz";
      binary = "codex-aarch64-unknown-linux-musl";
      hash = "sha256-G5yuluJ/XaJ1IFSlu6kgTUhpOepgxl30ukpjhFhzS9o=";
    };
  };

  system = stdenvNoCC.hostPlatform.system;
  sourceForSystem = sources.${system} or (throw "codex-cli: unsupported system ${system}");
in
stdenvNoCC.mkDerivation {
  pname = "codex";
  inherit version;

  src = fetchurl {
    url = "https://github.com/openai/codex/releases/download/${releaseTag}/${sourceForSystem.asset}";
    hash = sourceForSystem.hash;
  };

  sourceRoot = ".";
  unpackCmd = "tar -xzf $src";

  dontConfigure = true;
  dontBuild = true;

  installPhase = ''
    runHook preInstall

    install -Dm755 "${sourceForSystem.binary}" "$out/bin/codex"

    runHook postInstall
  '';

  meta = {
    description = "Lightweight coding agent that runs in your terminal";
    homepage = "https://github.com/openai/codex";
    license = lib.licenses.asl20;
    mainProgram = "codex";
    platforms = builtins.attrNames sources;
  };
}
