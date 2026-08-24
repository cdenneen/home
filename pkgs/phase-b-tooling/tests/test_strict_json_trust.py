from __future__ import annotations

import hashlib
import json
import os
import tempfile
import unittest
from copy import deepcopy
from pathlib import Path
from typing import Any
from unittest.mock import patch

from common import ATTEMPT, Fixture, signed
from phase_b import strict_json
from phase_b.artifacts import ArtifactError, DirectoryArtifactStore
from phase_b.execute_cli import main as execute_main
from phase_b.trust import (
    HMACSHA256Verifier,
    TrustError,
    _load_trust_anchor_at,
    _parse_anchor,
    verify_envelope,
    verify_executable,
)


class StrictJSONTests(unittest.TestCase):
    def test_rejects_duplicate_nonfinite_trailing_invalid_utf8_and_bounds(self) -> None:
        for value in (b'{"x":1,"x":2}', b'{"x":NaN}', b"{} trailing", b"\xff"):
            with (
                self.subTest(value=value),
                self.assertRaises(strict_json.StrictJSONError),
            ):
                strict_json.loads(value)
        with self.assertRaises(strict_json.StrictJSONError):
            strict_json.loads(b'"' + b"x" * 20 + b'"', maximum=10)
        self.assertEqual(strict_json.canonical({"b": 1, "a": 2}), b'{"a":2,"b":1}')

    def test_signed_bytes_must_be_exact_canonical_encoding(self) -> None:
        value = {"schema": "phase-b.signed-envelope.v1", "payload": {"x": 1}}
        self.assertEqual(
            strict_json.loads_canonical(strict_json.canonical(value)), value
        )
        for encoded in (
            json.dumps(value).encode(),
            strict_json.canonical(value) + b"\n",
        ):
            with self.assertRaises(strict_json.StrictJSONError):
                strict_json.loads_canonical(encoded)

    def test_schema_is_strict_and_bool_is_not_integer(self) -> None:
        schema = {
            "type": "object",
            "required": ["count"],
            "properties": {"count": {"type": "integer"}},
            "additionalProperties": False,
        }
        strict_json.validate({"count": 1}, schema)
        for value in ({"count": True}, {"count": 1, "extra": 2}):
            with self.assertRaises(strict_json.StrictJSONError):
                strict_json.validate(value, schema)


class ArtifactWriteTests(unittest.TestCase):
    def test_live_capture_write_is_atomic_owner_only_and_idempotent(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            secure_root = Path(temporary)
            secure_root.chmod(0o700)
            root = secure_root / "evidence"
            root.mkdir(mode=0o700)
            store = DirectoryArtifactStore(
                root, owner_uid=os.getuid(), secure_root=secure_root
            )
            data = strict_json.canonical({"capture": "live"})
            first = store.write("f0-audit", data)
            second = store.write("f0-audit", data)
            self.assertEqual(first, second)
            path = root / f"{first.id}.artifact"
            self.assertEqual(path.stat().st_mode & 0o777, 0o400)
            self.assertEqual(path.stat().st_nlink, 1)
            self.assertEqual(store.read(first.id), data)

            path.unlink()
            path.symlink_to(root / "missing")
            with self.assertRaises(ArtifactError):
                store.write("f0-audit", data)


class TrustTests(unittest.TestCase):
    def setUp(self) -> None:
        self.fixture = Fixture()
        self.addCleanup(self.fixture.close)
        self.anchor = self.fixture.anchor()

    def anchor_value(self) -> dict[str, Any]:
        anchor = self.anchor
        return {
            "schema": "phase-b.trust.v2",
            "anchor_generation": anchor.anchor_generation,
            "signers": {
                role: {
                    "id": item.signer_id,
                    "algorithm": item.algorithm,
                    "public_key": item.public_key,
                }
                for role, item in anchor.signers.items()
            },
            "namespace_roles": anchor.namespace_roles,
            "executables": {
                name: {
                    "path": str(item.path),
                    "closure": str(item.closure),
                    "digest": item.digest,
                }
                for name, item in anchor.executables.items()
            },
            "source": {
                "uid": anchor.source.uid,
                "gid": anchor.source.gid,
                "user": anchor.source.user,
                "home": anchor.source.home,
                "machine_id": anchor.source.machine_id,
                "host_identity": anchor.source.host_identity,
                "boot_id": anchor.source.boot_id,
                "user_manager_id": anchor.source.user_manager_id,
                "home_generation": anchor.source.home_generation,
                "booted_closure": anchor.source.booted_closure,
                "user_manager_machine": anchor.source.user_manager_machine,
            },
            "registry_paths": list(anchor.registry_paths),
            "collector_identity": anchor.collector_identity,
            "authority_identities": anchor.authority_identities,
            "effect_plan_digest": anchor.effect_plan_digest,
            "rollback_plan_digest": anchor.rollback_plan_digest,
            "canonical_vectors_digest": anchor.canonical_vectors_digest,
            "process_inventory_digest": anchor.process_inventory_digest,
            "listener_inventory_digest": anchor.listener_inventory_digest,
            "runbook_digest": anchor.runbook_digest,
            "schema_digests": anchor.schema_digests,
        }

    def test_root_owned_chain_anchor_and_role_signature(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            directory = root / "etc" / "phase-b"
            directory.mkdir(parents=True)
            directory.chmod(0o700)
            path = directory / "trust.json"
            path.write_text(json.dumps(self.anchor_value()), encoding="utf-8")
            path.chmod(0o400)
            loaded = _load_trust_anchor_at(path, root=root, owner_uid=os.getuid())
            payload = {"attempt_id": ATTEMPT}
            self.assertEqual(
                verify_envelope(
                    signed(payload, "phase-b-baseline"),
                    loaded,
                    HMACSHA256Verifier(),
                    "phase-b-baseline",
                ),
                payload,
            )

    def test_distinct_role_keys_signers_and_fixed_namespace_mapping(self) -> None:
        for defect in ("same-key", "same-id", "mapping", "missing-role"):
            with self.subTest(defect=defect):
                value = deepcopy(self.anchor_value())
                roles = sorted(value["signers"])
                if defect == "same-key":
                    value["signers"][roles[1]]["public_key"] = value["signers"][
                        roles[0]
                    ]["public_key"]
                elif defect == "same-id":
                    value["signers"][roles[1]]["id"] = value["signers"][roles[0]]["id"]
                elif defect == "mapping":
                    value["namespace_roles"]["phase-b-receipt"] = "source-execution"
                else:
                    value["signers"].pop(roles[0])
                with self.assertRaises(TrustError):
                    _parse_anchor(value)

    def test_cross_role_or_tampered_envelope_fails(self) -> None:
        payload = {"attempt_id": ATTEMPT}
        envelope = signed(payload, "phase-b-f0")
        with self.assertRaises(TrustError):
            verify_envelope(
                envelope, self.anchor, HMACSHA256Verifier(), "phase-b-baseline"
            )
        changed = deepcopy(envelope)
        changed["payload"]["attempt_id"] = "phase-b-fixture-999"
        with self.assertRaises(TrustError):
            verify_envelope(changed, self.anchor, HMACSHA256Verifier(), "phase-b-f0")

    def test_anchor_rejects_writable_parent_symlink_hardlink_and_wrong_mode(
        self,
    ) -> None:
        for defect in ("writable-parent", "symlink", "hardlink", "wrong-mode"):
            with (
                self.subTest(defect=defect),
                tempfile.TemporaryDirectory() as temporary,
            ):
                root = Path(temporary)
                directory = root / "etc" / "phase-b"
                directory.mkdir(parents=True)
                directory.chmod(0o700)
                real = directory / "real.json"
                real.write_text(json.dumps(self.anchor_value()), encoding="utf-8")
                real.chmod(0o400)
                path = directory / "trust.json"
                if defect == "symlink":
                    path.symlink_to(real.name)
                elif defect == "hardlink":
                    os.link(real, path)
                else:
                    path.write_bytes(real.read_bytes())
                    path.chmod(0o400)
                if defect == "writable-parent":
                    directory.chmod(0o777)
                if defect == "wrong-mode":
                    path.chmod(0o600)
                with self.assertRaises(TrustError):
                    _load_trust_anchor_at(path, root=root, owner_uid=os.getuid())

    def test_exact_executable_content_binding(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            store = Path(temporary)
            store.chmod(0o755)
            binding = self.anchor.executables["signature-verifier"]
            closure = store / binding.closure.name
            executable = closure / "bin" / "signature-verifier"
            executable.parent.mkdir(parents=True)
            executable.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
            executable.chmod(0o555)
            from dataclasses import replace

            actual = replace(
                binding,
                digest="sha256:" + hashlib.sha256(executable.read_bytes()).hexdigest(),
            )
            verify_executable(actual, store_root=store, owner_uid=os.getuid())
            executable.chmod(0o755)
            executable.write_text("changed", encoding="utf-8")
            executable.chmod(0o555)
            with self.assertRaises(TrustError):
                verify_executable(actual, store_root=store, owner_uid=os.getuid())

    def test_o_nofollow_absence_fails_closed(self) -> None:
        with patch("phase_b.trust.os.O_NOFOLLOW", 0), self.assertRaises(TrustError):
            _load_trust_anchor_at(Path("/missing"), root=Path("/"), owner_uid=0)

    def test_production_cli_rejects_options_and_missing_anchor(self) -> None:
        with patch("sys.argv", ["phase-b-execute", "--baseline", "/tmp/operator.json"]):
            self.assertEqual(execute_main(), 64)
        with (
            patch("sys.argv", ["phase-b-execute"]),
            patch(
                "phase_b.cli_common.load_trust_anchor",
                side_effect=TrustError("absent"),
            ),
        ):
            self.assertEqual(execute_main(), 1)


if __name__ == "__main__":
    unittest.main()
