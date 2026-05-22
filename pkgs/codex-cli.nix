{
  fetchurl,
  lib,
  stdenvNoCC,
}:

let
  version = "0.129.0";
  releaseTag = "rust-v${version}";

  sources = {
    aarch64-darwin = {
      asset = "codex-aarch64-apple-darwin.tar.gz";
      binary = "codex-aarch64-apple-darwin";
      hash = "sha256-Fj/72NP0nQeY1cIiT8auqJhd+/l0fxb7VEIMtN43OxU=";
    };
    x86_64-darwin = {
      asset = "codex-x86_64-apple-darwin.tar.gz";
      binary = "codex-x86_64-apple-darwin";
      hash = "sha256-NXOKHW/1UUeNeBrX4d5ssIg8UoKWvyQ0svwVdKAL2C4=";
    };
    x86_64-linux = {
      asset = "codex-x86_64-unknown-linux-musl.tar.gz";
      binary = "codex-x86_64-unknown-linux-musl";
      hash = "sha256-Skoo0i3R+HTix7I9m6E9sCv3rU7ppwucTqq2GHCNBYI=";
    };
    aarch64-linux = {
      asset = "codex-aarch64-unknown-linux-musl.tar.gz";
      binary = "codex-aarch64-unknown-linux-musl";
      hash = "sha256-YO2eVVaaTCh9ASzfvUN0Kiv7EwZz4ixjtrs4xo+qc2A=";
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
