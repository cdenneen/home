{ rustPlatform, fetchFromGitHub }:

rustPlatform.buildRustPackage {
  pname = "rtk";
  version = "0.37.2";

  src = fetchFromGitHub {
    owner = "rtk-ai";
    repo = "rtk";
    rev = "v0.37.2";
    hash = "sha256-rNuu8B5TnKZHrbVSV8HkcTeTdcol26259GGJEPEMPZY=";
  };

  cargoHash = "sha256-61+PNuVF8H5+9PHc3MBt8V80ieBBi8HzSC9Gc/WUSzM=";

  doCheck = false;
}
