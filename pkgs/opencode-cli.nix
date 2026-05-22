{
  fetchurl,
  lib,
  stdenvNoCC,
  unzip,
}:

let
  version = "1.14.41";

  sources = {
    aarch64-darwin = {
      asset = "opencode-darwin-arm64.zip";
      hash = "sha256-eVYKWj8c+WU4s37HiuP5IybKU2p7taL26MHn6aK2/tI=";
    };
    x86_64-darwin = {
      asset = "opencode-darwin-x64.zip";
      hash = "sha256-/kfhKM5yDWlEH1iw1UX9lsUJKDFyBWIueofZYe5tHco=";
    };
    x86_64-linux = {
      asset = "opencode-linux-x64.tar.gz";
      hash = "sha256-0n08hRg6e9LfRQZISi9QjRiXliBjt8zIRmcFtJOWPcU=";
    };
    aarch64-linux = {
      asset = "opencode-linux-arm64.tar.gz";
      hash = "sha256-L/pju2EV16oZPLH2+nZut54bOZd2hxpiSTWnUuRGEQU=";
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
