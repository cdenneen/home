{
  codex,
  git,
  python3,
  stdenvNoCC,
}:
stdenvNoCC.mkDerivation {
  pname = "alpha0-node";
  version = "0.1.0";
  dontUnpack = true;

  installPhase = ''
    runHook preInstall
    install -Dm0555 ${./alpha0-node.py} $out/bin/alpha0-node
    install -Dm0555 ${./alpha0-node-inspect.py} $out/bin/alpha0-node-inspect
    install -Dm0555 ${./alpha0-node-codex-plan.py} $out/bin/alpha0-node-codex-plan
    substituteInPlace $out/bin/alpha0-node $out/bin/alpha0-node-inspect $out/bin/alpha0-node-codex-plan \
      --replace-fail '@python@' '${python3}/bin/python3' \
      --replace-fail '@git@' '${git}/bin/git'
    substituteInPlace $out/bin/alpha0-node-codex-plan \
      --replace-fail '@codex@' '${codex}/bin/codex'
    runHook postInstall
  '';

  doInstallCheck = true;
  installCheckPhase = ''
    $out/bin/alpha0-node --self-test
    ${python3}/bin/python3 -m py_compile \
      $out/bin/alpha0-node \
      $out/bin/alpha0-node-inspect \
      $out/bin/alpha0-node-codex-plan
    ${python3}/bin/python3 ${./test-alpha0-node.py} \
      $out/bin/alpha0-node \
      $out/bin/alpha0-node-inspect \
      ${git}/bin/git
    ${python3}/bin/python3 ${./test-alpha0-node-codex-plan.py} \
      $out/bin/alpha0-node-codex-plan \
      ${git}/bin/git
  '';
}
