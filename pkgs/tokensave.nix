{
  fetchCrate,
  lib,
  openssl,
  pkg-config,
  rustPlatform,
}:

rustPlatform.buildRustPackage rec {
  pname = "tokensave";
  version = "7.8.1";

  src = fetchCrate {
    inherit pname version;
    hash = "sha256-LUGLeCZR3KAEMM0NesrQTgj5DZD96bVVsw9XH+csZ4U=";
  };

  cargoHash = "sha256-9JP+XwGvFGqtbKZ8Co1VSbZH1CzeE0gJeGUF9UM/hw8=";

  nativeBuildInputs = [ pkg-config ];
  buildInputs = [ openssl ];

  # The upstream test phase exceeds 30 minutes even with its lite feature.
  # Keep the reproducible full-feature build and smoke-test the installed CLI.
  doCheck = false;
  doInstallCheck = true;
  installCheckPhase = ''
    runHook preInstallCheck
    $out/bin/tokensave --version | grep -F "tokensave ${version}"
    runHook postInstallCheck
  '';

  meta = {
    description = "Semantic code-intelligence knowledge graph";
    homepage = "https://github.com/aovestdipaperino/tokensave";
    license = lib.licenses.mit;
    mainProgram = "tokensave";
    platforms = lib.platforms.linux ++ lib.platforms.darwin;
  };
}
