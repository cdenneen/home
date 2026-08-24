{
  lib,
  python3,
  stdenvNoCC,
}:
stdenvNoCC.mkDerivation {
  pname = "phase-b-tooling";
  version = "0.1.0";
  src = ./phase-b-tooling;

  nativeBuildInputs = [ python3 ];

  installPhase = ''
        runHook preInstall

        install -d "$out/lib/phase-b/tests" "$out/bin" "$out/share/phase-b/schemas"
        cp -R phase_b "$out/lib/phase-b/"
        cp phase_b/schemas/*.schema.json "$out/share/phase-b/schemas/"
        cp tests/*.py "$out/lib/phase-b/tests/"

        install_cli() {
          local name="$1"
          local module="$2"
          cat > "$out/bin/$name" <<EOF
    #!${python3}/bin/python3 -I
    import sys
    sys.path.insert(0, "$out/lib/phase-b")
    from phase_b.$module import main
    raise SystemExit(main())
    EOF
          chmod 0555 "$out/bin/$name"
        }

        install_cli phase-b-execute execute_cli
        install_cli phase-b-collect collect_cli
        install_cli phase-b-verify verify_cli

        runHook postInstall
  '';

  doInstallCheck = true;
  installCheckPhase = ''
    runHook preInstallCheck

    export PYTHONPATH="$out/lib/phase-b"
    ${python3}/bin/python3 -m compileall -q "$out/lib/phase-b"
    ${python3}/bin/python3 -m unittest discover -s tests -v

    # Production entry points accept no caller-selected paths, clocks, commands,
    # or trust roots and fail closed without the root-managed anchor.
    set +e
    $out/bin/phase-b-execute --help >/dev/null 2>&1
    execute_status=$?
    $out/bin/phase-b-collect --trust /tmp/anchor >/dev/null 2>&1
    collect_status=$?
    $out/bin/phase-b-verify /tmp/bundle >/dev/null 2>&1
    verify_status=$?
    set -e
    test "$execute_status" = 64
    test "$collect_status" = 64
    test "$verify_status" = 64

    runHook postInstallCheck
  '';

  meta = {
    description = "Dormant root-anchored Phase B qualification tooling";
    license = lib.licenses.mit;
    platforms = lib.platforms.linux;
    mainProgram = "phase-b-verify";
  };
}
