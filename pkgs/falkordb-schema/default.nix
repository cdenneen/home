{ lib, python3Packages }:

python3Packages.buildPythonPackage rec {
  pname = "falkordb-schema";
  version = "0.1.0";
  format = "pyproject";

  src = ./.;

  nativeBuildInputs = with python3Packages; [
    setuptools
    pip
  ];

  propagatedBuildInputs = with python3Packages; [
    redis
  ];

  pythonImportsCheck = [ "falkordb_schema" ];

  meta = {
    description = "FalkorDB knowledge graph schema and utilities";
    homepage = "https://github.com/cdenneen/home";
    license = lib.licenses.mit;
    maintainers = [ "cdenneen" ];
  };
}
