{
  fetchurl,
  lib,
  stdenvNoCC,
  unzip,
}:

let
  version = "1.16.0";

  sources = {
    aarch64-darwin = {
      asset = "opencode-darwin-arm64.zip";
      hash = "sha256-N7xrybVN59/K70DxnfgiJ23fFCrzijpzIl7stXZ+nyY=";
    };
    x86_64-darwin = {
      asset = "opencode-darwin-x64.zip";
      hash = "sha256-/1NZlkW60UEE4IPkS23Ug0XERawGvi55qPwKzYFP7qk=";
    };
    x86_64-linux = {
      asset = "opencode-linux-x64.tar.gz";
      hash = "sha256-p0HEPnN7IDP15+4VGxYjQeRBA01qZLFyJyo/Ojcp6H0=";
    };
    aarch64-linux = {
      asset = "opencode-linux-arm64.tar.gz";
      hash = "sha256-Bu9gK5vIpiT9yOknZz59qky3Dx5XxVhAI8qTYxxKR24=";
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
