{
  fetchurl,
  lib,
  stdenvNoCC,
  unzip,
}:

let
  version = "1.18.8";

  sources = {
    aarch64-darwin = {
      asset = "opencode-darwin-arm64.zip";
      hash = "sha256-D7LhGoGd2XlJ8PfgNI4ODE/YxCs6Xteu4fDUN8lLnww=";
    };
    x86_64-darwin = {
      asset = "opencode-darwin-x64.zip";
      hash = "sha256-AZPtPylbuT8HOuDo+gc36bMfFnRkdhkBWJQB/SeNTMQ=";
    };
    x86_64-linux = {
      asset = "opencode-linux-x64.tar.gz";
      hash = "sha256-tyAUuLU0J/21pijSQzVp7nzNKJvVxEkGNgZLJHkcEwU=";
    };
    aarch64-linux = {
      asset = "opencode-linux-arm64.tar.gz";
      hash = "sha256-PhtPO9EnZMkR+SEZEGCPhUKbYgmQCmYsftJxlskDO5M=";
    };
  };

  system = stdenvNoCC.hostPlatform.system;
  sourceForSystem = sources.${system} or (throw "opencode-cli: unsupported system ${system}");
in
stdenvNoCC.mkDerivation {
  pname = "opencode";
  inherit version;

  src = fetchurl {
    url = "https://github.com/sst/opencode/releases/download/v${version}/${sourceForSystem.asset}";
    hash = sourceForSystem.hash;
  };

  sourceRoot = ".";
  nativeBuildInputs = [ unzip ];

  unpackPhase =
    if lib.hasSuffix ".zip" sourceForSystem.asset then
      ''
        runHook preUnpack
        unzip -q "$src"
        runHook postUnpack
      ''
    else
      ''
        runHook preUnpack
        tar -xzf "$src"
        runHook postUnpack
      '';

  dontConfigure = true;
  dontBuild = true;

  installPhase = ''
    runHook preInstall

    install -Dm755 opencode "$out/bin/opencode"

    runHook postInstall
  '';

  meta = {
    description = "AI coding agent in your terminal";
    homepage = "https://opencode.ai";
    license = lib.licenses.mit;
    mainProgram = "opencode";
    platforms = builtins.attrNames sources;
  };
}
