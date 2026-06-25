{
  fetchurl,
  lib,
  stdenvNoCC,
}:

let
  version = "0.142.0";
  releaseTag = "rust-v${version}";

  sources = {
    aarch64-darwin = {
      asset = "codex-aarch64-apple-darwin.tar.gz";
      binary = "codex-aarch64-apple-darwin";
      hash = "sha256-2qREPEVfSBQ9dQkS+g+R17lFb6UpcvclvBJUrptaNkg=";
    };
    x86_64-darwin = {
      asset = "codex-x86_64-apple-darwin.tar.gz";
      binary = "codex-x86_64-apple-darwin";
      hash = "sha256-IBQaWLHgd7I/A4fpmvw9digOzWyS72gzQ0Sgo3nSkzY=";
    };
    x86_64-linux = {
      asset = "codex-x86_64-unknown-linux-musl.tar.gz";
      binary = "codex-x86_64-unknown-linux-musl";
      hash = "sha256-LjrLOaJ3/xHDFNgyz90kb66+6ia/Aa/46eEGQebeqAE=";
    };
    aarch64-linux = {
      asset = "codex-aarch64-unknown-linux-musl.tar.gz";
      binary = "codex-aarch64-unknown-linux-musl";
      hash = "sha256-Y/yYFvF0q09xMDHmOCAcSc+nzF9BoiudtxAQr6fgmJI=";
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
