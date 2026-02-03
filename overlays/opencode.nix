final: prev:
let
  version = "1.1.49";
in
{
  opencode = prev.stdenv.mkDerivation {
    pname = "opencode";
    inherit version;

    src =
      if prev.stdenv.isDarwin then
        prev.fetchzip {
          url = "https://github.com/anomalyco/opencode/releases/download/v${version}/opencode-darwin-arm64.zip";
          sha256 = "1ysh1n9j12lpq73b0nks5hzg49597i79nmp6f7s5kpgdf7i0wf4p";
          stripRoot = false;
        }
      else if prev.stdenv.isLinux && prev.stdenv.isAarch64 then
        # Upstream linux arm64 tarball ships a bun-based wrapper.
        # Use nixpkgs source build for a native binary instead.
        prev.opencode.src
      else
        prev.opencode.src;

    installPhase = ''
      mkdir -p $out/bin
      cp opencode $out/bin/opencode
      chmod +x $out/bin/opencode
    '';

    meta = with prev.lib; {
      description = "OpenCode CLI (prebuilt binary)";
      homepage = "https://github.com/anomalyco/opencode";
      platforms = [
        "aarch64-darwin"
        "aarch64-linux"
      ];
    };
  };
}
