{
  fetchurl,
  lib,
  stdenvNoCC,
  unzip,
}:

let
  version = "1.17.13";

  sources = {
    aarch64-darwin = {
      asset = "opencode-darwin-arm64.zip";
      hash = "sha256-3QFtPiazR9Z1qybEXR4odUWRLVxMSfoHcLYi1KE2fiM=";
    };
    x86_64-darwin = {
      asset = "opencode-darwin-x64.zip";
      hash = "sha256-C/PZ0TQJfKaYuD9kxV25YNbS0MQJBpv0z9hj5d5QO0o=";
    };
    x86_64-linux = {
      asset = "opencode-linux-x64.tar.gz";
      hash = "sha256-FXr6KJ0ajZNy3gzhmscmEZuTeh9rIBgI1G8G5OWbs0g=";
    };
    aarch64-linux = {
      asset = "opencode-linux-arm64.tar.gz";
      hash = "sha256-u6zN03Sqq2bNl8f4rRwICqOTYQ+l+A7o38AH+VAK+vk=";
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
