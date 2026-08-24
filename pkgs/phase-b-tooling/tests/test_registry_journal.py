from __future__ import annotations

import fcntl
import os
import tempfile
import unittest
from copy import deepcopy
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

from common import Fixture
from phase_b import journal as journal_module
from phase_b import strict_json
from phase_b.journal import Journal, JournalError
from phase_b.registry import (
    FIXED_DELTAS,
    RegistryError,
    RegistrySet,
    _validate_applied_document,
)


class RegistryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.fixture = Fixture()
        self.addCleanup(self.fixture.close)

    def build(self) -> RegistrySet:
        result = RegistrySet(
            self.fixture.expectations(), tuple(str(path) for path in self.fixture.paths)
        )
        result.acquire()
        self.addCleanup(result.close)
        return result

    def pause(self, delta, ordinal: int) -> None:
        path = self.fixture.paths[delta.registry_index]
        document = strict_json.loads(path.read_bytes())
        for job in document["jobs"]:
            if job["id"] == delta.job_id:
                job.update(
                    {
                        "enabled": False,
                        "state": "paused",
                        "paused_at": f"2026-08-20T00:00:0{ordinal}+00:00",
                        "paused_reason": None,
                    }
                )
        document["updated_at"] = f"2026-08-20T00:00:0{ordinal}+00:00"
        temporary = path.with_name(f".jobs_{ordinal}.tmp")
        temporary.write_bytes(strict_json.canonical(document) + b"\n")
        temporary.chmod(0o600)
        os.replace(temporary, path)

    def test_target_non_pause_metadata_distinguishes_missing_from_null(self) -> None:
        baseline = {
            "jobs": [
                {
                    "id": "target",
                    "enabled": True,
                    "description": None,
                }
            ]
        }
        paused = {
            "jobs": [
                {
                    "id": "target",
                    "enabled": False,
                    "state": "paused",
                    "paused_at": "2026-08-20T00:00:00Z",
                    "paused_reason": None,
                    "description": None,
                }
            ],
            "updated_at": "2026-08-20T00:00:00Z",
        }
        _validate_applied_document(baseline, paused, ("target",))

        missing_reason = deepcopy(paused)
        del missing_reason["jobs"][0]["paused_reason"]
        with self.assertRaisesRegex(RegistryError, "pause state/reason"):
            _validate_applied_document(baseline, missing_reason, ("target",))

        removed_null = deepcopy(paused)
        del removed_null["jobs"][0]["description"]
        with self.assertRaisesRegex(RegistryError, "non-pause target metadata"):
            _validate_applied_document(baseline, removed_null, ("target",))

        baseline_without = deepcopy(baseline)
        del baseline_without["jobs"][0]["description"]
        added_null = deepcopy(paused)
        with self.assertRaisesRegex(RegistryError, "non-pause target metadata"):
            _validate_applied_document(baseline_without, added_null, ("target",))

    def test_acquires_six_pinned_parents_and_hermes_locks(self) -> None:
        registries = self.build()
        self.assertEqual(len(registries.handles), 6)
        self.assertEqual(len({item.identity for item in registries.handles}), 6)
        with registries._locks():
            for handle in registries.handles:
                other = os.open(".jobs.lock", os.O_RDWR, dir_fd=handle.parent_fd)
                try:
                    with self.assertRaises(BlockingIOError):
                        fcntl.flock(other, fcntl.LOCK_EX | fcntl.LOCK_NB)
                finally:
                    os.close(other)
        home, profile, pass_fds = registries.hermes_invocation(FIXED_DELTAS[0])
        self.assertEqual(home, f"/proc/self/fd/{pass_fds[0]}")
        self.assertIsNone(profile)
        self.assertEqual(pass_fds[-1], registries.handles[0].lock_fd)
        self.assertTrue(all(os.fstat(fd) for fd in pass_fds))

    def test_full_applied_prefix_accepts_historical_inode_reuse(self) -> None:
        expectations = self.fixture.expectations()
        active = RegistrySet(
            expectations, tuple(str(path) for path in self.fixture.paths)
        )
        active.acquire()
        for ordinal, delta in enumerate(FIXED_DELTAS, 1):
            self.pause(delta, ordinal)
            active.revalidate(FIXED_DELTAS[:ordinal], changed_delta=delta)
        active.close()

        # Model filesystem inode reuse by making each historical signed inode
        # equal the current post-replacement inode while retaining its baseline
        # document. Recovery must rely on the exact applied document, not inequality.
        reused = tuple(
            replace(
                expectation,
                device=self.fixture.paths[index].stat().st_dev,
                inode=self.fixture.paths[index].stat().st_ino,
            )
            if index in {delta.registry_index for delta in FIXED_DELTAS}
            else expectation
            for index, expectation in enumerate(expectations)
        )
        recovered = RegistrySet(
            reused, tuple(str(path) for path in self.fixture.paths)
        )
        self.addCleanup(recovered.close)
        recovered.acquire(FIXED_DELTAS)
        recovered.revalidate(FIXED_DELTAS)
        self.assertEqual(len(recovered.handles), 6)

    def test_lock_path_replacement_after_flock_fails_revalidation(self) -> None:
        registries = self.build()
        replacement = self.fixture.paths[0].parent / ".lock-new"
        replacement.write_bytes(b"")
        replacement.chmod(0o600)
        os.replace(replacement, replacement.with_name(".jobs.lock"))
        with self.assertRaises(RegistryError):
            registries.revalidate()

    def test_exact_atomic_replace_pause_sequence_is_accepted(self) -> None:
        registries = self.build()
        applied = []
        for ordinal, delta in enumerate(FIXED_DELTAS, 1):
            self.pause(delta, ordinal)
            applied.append(delta)
            digests = registries.revalidate(tuple(applied), changed_delta=delta)
            self.assertEqual(digests, registries.last_digests)
            self.assertEqual(
                registries.evidence()[delta.registry_index]["digest"],
                digests[delta.registry_index],
            )

    def test_in_place_write_and_parent_or_path_replacement_fail(self) -> None:
        registries = self.build()
        path = self.fixture.paths[0]
        path.write_bytes(path.read_bytes() + b" ")
        with self.assertRaises(RegistryError):
            registries.revalidate()
        # Fresh fixture for atomic replacement with no declared delta.
        fixture = Fixture()
        try:
            other = RegistrySet(
                fixture.expectations(), tuple(str(path) for path in fixture.paths)
            )
            other.acquire()
            try:
                source = fixture.paths[0]
                temporary = source.with_name("replacement.tmp")
                temporary.write_bytes(source.read_bytes())
                temporary.chmod(0o600)
                os.replace(temporary, source)
                with self.assertRaises(RegistryError):
                    other.revalidate()
            finally:
                other.close()
        finally:
            fixture.close()

    def test_alias_symlink_mode_duplicate_json_and_non_target_delta_fail(self) -> None:
        defects = ("alias", "symlink", "mode", "duplicate", "non-target")
        for defect in defects:
            with self.subTest(defect=defect):
                fixture = Fixture()
                try:
                    expectations = list(fixture.expectations())
                    if defect == "alias":
                        fixture.paths[5].unlink()
                        os.link(fixture.paths[4], fixture.paths[5])
                        expectations = list(fixture.expectations())
                    elif defect == "symlink":
                        fixture.paths[5].unlink()
                        fixture.paths[5].symlink_to(fixture.paths[4])
                    elif defect == "mode":
                        fixture.paths[5].chmod(0o644)
                    elif defect == "duplicate":
                        fixture.paths[5].write_text(
                            '{"jobs":[],"jobs":[]}', encoding="utf-8"
                        )
                    if defect == "alias":
                        with self.assertRaises(RegistryError):
                            RegistrySet(
                                tuple(expectations),
                                tuple(str(path) for path in fixture.paths),
                            )
                        continue
                    registry = RegistrySet(
                        tuple(expectations), tuple(str(path) for path in fixture.paths)
                    )
                    if defect == "non-target":
                        registry.acquire()
                        try:
                            path = fixture.paths[1]
                            doc = strict_json.loads(path.read_bytes())
                            doc["jobs"][0]["enabled"] = False
                            doc["updated_at"] = "2026-08-20T00:00:00+00:00"
                            temporary = path.with_name(".jobs_x.tmp")
                            temporary.write_bytes(strict_json.canonical(doc))
                            temporary.chmod(0o600)
                            os.replace(temporary, path)
                            with self.assertRaises(RegistryError):
                                registry.revalidate((), changed_delta=FIXED_DELTAS[0])
                        finally:
                            registry.close()
                    else:
                        with self.assertRaises((RegistryError, OSError)):
                            registry.acquire()
                finally:
                    fixture.close()


class JournalTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.path = Path(self.temporary.name) / "journal"

    def test_intent_outcome_hash_chain(self) -> None:
        journal = Journal(self.path)
        first = journal.append("intent", "effect-1", {"operation": "fixed"})
        self.assertIn("effect-1", journal.pending_intents())
        second = journal.append("outcome", "effect-1", {"status": "achieved"})
        self.assertEqual(second["previous_hash"], first["record_hash"])
        self.assertEqual(journal.pending_intents(), {})

    def test_anonymous_publication_fault_boundaries_are_atomic(self) -> None:
        stages = (
            "before-anonymous-create",
            "after-anonymous-create",
            "before-anonymous-write",
            "after-anonymous-write",
            "before-anonymous-fsync",
            "after-anonymous-fsync",
            "before-link",
            "after-link",
            "before-directory-fsync",
            "after-directory-fsync",
        )
        linked = {
            "after-link",
            "before-directory-fsync",
            "after-directory-fsync",
        }
        for stage in stages:
            with self.subTest(stage=stage), tempfile.TemporaryDirectory() as temporary:

                def fault(current: str, target: str = stage) -> None:
                    if current == target:
                        raise RuntimeError("injected")

                journal = Journal(Path(temporary) / "journal", fault=fault)
                with self.assertRaises(RuntimeError):
                    journal.append("checkpoint", "x", {})
                records = journal.read_all()
                self.assertEqual(len(records), 1 if stage in linked else 0)
                self.assertEqual(journal.orphan_temps(), ())
                self.assertEqual(
                    sorted(path.name for path in journal.directory.iterdir()),
                    ["0000000000000000.json"] if stage in linked else [],
                )

    def test_fast_checkpoint_uses_the_same_anonymous_atomic_publication(self) -> None:
        for stage, expected in (("before-link", 0), ("after-link", 1)):
            with self.subTest(stage=stage), tempfile.TemporaryDirectory() as temporary:

                def fault(current: str, target: str = stage) -> None:
                    if current == target:
                        raise RuntimeError("injected")

                journal = Journal(Path(temporary) / "journal", fault=fault)
                with self.assertRaises(RuntimeError):
                    journal.append_checkpoint_fast(
                        "x",
                        {},
                        expected_sequence=0,
                        expected_head=journal_module.ZERO_HASH,
                    )
                self.assertEqual(len(journal.read_all()), expected)
                self.assertEqual(journal.orphan_temps(), ())

    def test_anonymous_publication_never_overwrites_competing_destination(self) -> None:
        for competitor in ("file", "symlink"):
            with self.subTest(competitor=competitor), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp) / "journal"
                outside = Path(tmp) / "outside"
                outside.write_bytes(b"outside")

                def inject(
                    stage: str,
                    target_root: Path = root,
                    target_kind: str = competitor,
                    outside_path: Path = outside,
                ) -> None:
                    if stage != "before-link":
                        return
                    destination = target_root / "0000000000000000.json"
                    if target_kind == "file":
                        destination.write_bytes(b"competitor")
                        destination.chmod(0o600)
                    else:
                        destination.symlink_to(outside_path)

                journal = Journal(root, fault=inject)
                with self.assertRaisesRegex(
                    JournalError, "concurrently claimed"
                ):
                    journal.append("checkpoint", "x", {})
                destination = root / "0000000000000000.json"
                if competitor == "file":
                    self.assertEqual(destination.read_bytes(), b"competitor")
                else:
                    self.assertTrue(destination.is_symlink())
                    self.assertEqual(outside.read_bytes(), b"outside")

    def test_publication_verifies_final_inode_is_anonymous_source(self) -> None:
        journal = Journal(self.path)

        def publish_other(_fd: int, directory: int, final_name: str) -> None:
            other = os.open(
                final_name,
                os.O_WRONLY | os.O_CREAT | os.O_EXCL,
                0o600,
                dir_fd=directory,
            )
            os.close(other)

        with (
            patch.object(journal_module, "_link_anonymous", publish_other),
            self.assertRaisesRegex(JournalError, "differs from anonymous source"),
        ):
            journal.append("checkpoint", "x", {})

    def test_named_legacy_temporary_artifacts_are_rejected(self) -> None:
        names = (
            ".pending-0000000000000000-aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa.tmp",
            ".seal-legacy.tmp",
            ".sealed-legacy.evidence",
            ".quarantine-legacy.evidence",
            "sealed-orphan-legacy.evidence",
        )
        for name in names:
            with self.subTest(name=name), tempfile.TemporaryDirectory() as temporary:
                journal = Journal(Path(temporary) / "journal")
                journal.initialize()
                (journal.directory / name).write_bytes(b"legacy")
                self.assertEqual(journal.orphan_temps(), (name,))
                with self.assertRaisesRegex(JournalError, "unsupported named"):
                    journal.read_all()

    def test_tamper_gap_extra_hardlink_and_orphan_outcome_fail(self) -> None:
        journal = Journal(self.path)
        journal.append("checkpoint", "one", {})
        record = self.path / "0000000000000000.json"
        raw = bytearray(record.read_bytes())
        raw[-2] ^= 1
        record.write_bytes(raw)
        with self.assertRaises(JournalError):
            journal.read_all()
        with tempfile.TemporaryDirectory() as temporary:
            j = Journal(Path(temporary) / "j")
            with self.assertRaises(JournalError):
                j.append("outcome", "missing", {})
        with tempfile.TemporaryDirectory() as temporary:
            j = Journal(Path(temporary) / "j")
            j.append("checkpoint", "one", {})
            (j.directory / "extra").write_text("x", encoding="utf-8")
            with self.assertRaises(JournalError):
                j.read_all()


if __name__ == "__main__":
    unittest.main()
