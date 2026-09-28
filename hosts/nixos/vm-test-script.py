# NixOS test driver script for the platform prerequisite + R8 sealing matrix.
# Baseline A–F from approved package; section G extends EVERY SEALING-COVERAGE row.
# Workdir reviewable extension — approved package baseline unchanged.
import json
import re

AS_AXIS = "runuser -u axis -- "
SPOOL = "/var/lib/axis-acceptance"


def stage(job_id, module_src, argv):
    """Stage a job as AXIS will: as axis, into incoming/."""
    base = f"{SPOOL}/incoming/{job_id}"
    machine.succeed(f"{AS_AXIS} mkdir -p {base}/snapshot/src")
    machine.succeed(f"{AS_AXIS} tee {base}/snapshot/src/probe_mod.py > /dev/null << 'PYEOF'\n{module_src}\nPYEOF")
    case = json.dumps({"module": "probe_mod", "argv": argv})
    machine.succeed(f"{AS_AXIS} sh -c 'cat > {base}/case.json' << 'JEOF'\n{case}\nJEOF")


def seal_and_run(job_id):
    machine.succeed(f"{AS_AXIS} systemctl start axis-acceptance-seal@{job_id}.service")
    return machine.execute(f"{AS_AXIS} systemctl start --wait axis-acceptance@{job_id}.service")[0]


def result(job_id):
    return machine.succeed(f"cat {SPOOL}/jobs/{job_id}/result/stdout")


machine.wait_for_unit("multi-user.target", timeout=900)

# ---- Positive control: ordinary conforming job seals and executes ------------------------
with subtest("positive control: conforming job seals and executes"):
    stage("positive-ctl-01", "print('POSITIVE_OK')", [])
    rc = seal_and_run("positive-ctl-01")
    assert rc == 0, rc
    out = result("positive-ctl-01")
    assert "POSITIVE_OK" in out, out
    print("POSITIVE_CONTROL_PASS")

# ---- A. Permission scope -----------------------------------------------------------------
with subtest("axis may start and stop only the two acceptance templates"):
    # Positive: start/stop of acceptance templates is allowed (exercised by seal_and_run).
    # Denied ops must ASSERT non-zero — printing alone is not evidence.
    machine.fail(f"{AS_AXIS} systemctl start sshd.service")
    machine.fail(f"{AS_AXIS} systemctl stop polkit.service")
    machine.fail(f"{AS_AXIS} systemd-run --collect true")
    machine.fail(f"{AS_AXIS} systemctl daemon-reload")
    # manage-unit-files path (enable/disable) must be denied — not in start/stop contract.
    rc_en = machine.execute(f"{AS_AXIS} systemctl enable axis-acceptance@probe00001.service")[0]
    assert rc_en != 0, f"enable must be DENIED rc={rc_en}"
    rc_dis = machine.execute(f"{AS_AXIS} systemctl disable axis-acceptance@probe00001.service")[0]
    assert rc_dis != 0, f"disable must be DENIED rc={rc_dis}"
    for verb in ("restart", "reload", "kill", "try-restart", "reload-or-restart"):
        rc = machine.execute(f"{AS_AXIS} systemctl {verb} axis-acceptance@probe00001.service")[0]
        assert rc != 0, f"{verb} must be DENIED rc={rc}"
        print(f"DENIED verb={verb} rc={rc}")
    print("POLKIT_DENIES_ASSERTED enable/disable/restart/reload/kill")

# ---- B. Controller integrity -------------------------------------------------------------
with subtest("a case cannot modify controller files, evidence, checkout, or its own snapshot"):
    machine.succeed("install -d -o axis -g axis -m 0700 /var/lib/axis/ctl")
    for name in ("manifest.json", "evidence.json", "checkout.py"):
        machine.succeed(f"{AS_AXIS} sh -c 'echo ORIGINAL > /var/lib/axis/ctl/{name}'")
    probe = (
        "import sys\n"
        "targets = ['/var/lib/axis/ctl/manifest.json', '/var/lib/axis/ctl/evidence.json',\n"
        "           '/var/lib/axis/ctl/checkout.py', __file__]\n"
        "for t in targets:\n"
        "    try:\n"
        "        open(t, 'a').write('CHANGED')\n"
        "        print('WROTE', t)\n"
        "    except OSError:\n"
        "        print('BLOCKED', t)\n"
    )
    stage("write-probe-01", probe, [])
    seal_and_run("write-probe-01")
    out = result("write-probe-01")
    print(out)
    assert "WROTE" not in out, out
    for name in ("manifest.json", "evidence.json", "checkout.py"):
        machine.succeed(f"grep -qx ORIGINAL /var/lib/axis/ctl/{name}")

# ---- C. Sealed jobs fixed against controller ---------------------------------------------
with subtest("after sealing, axis cannot change the job it staged"):
    stage("seal-probe-01", "print('hello')", [])
    machine.succeed(f"{AS_AXIS} systemctl start axis-acceptance-seal@seal-probe-01.service")
    machine.fail(f"{AS_AXIS} sh -c 'echo x >> {SPOOL}/jobs/seal-probe-01/snapshot/src/probe_mod.py'")
    machine.fail(f"{AS_AXIS} sh -c 'echo x > {SPOOL}/jobs/seal-probe-01/case.json'")
    machine.fail(f"{AS_AXIS} test -e {SPOOL}/incoming/seal-probe-01")

# ---- D. Cleanup --------------------------------------------------------------------------
LINGER = (
    "import os, sys, time\n"
    "if os.fork() == 0:\n"
    "    os.setsid()\n"
    "    time.sleep(3600)\n"
    "    os._exit(0)\n"
    "mode = sys.argv[1]\n"
    "if mode == 'ok': print('done')\n"
    "elif mode == 'fail': sys.exit(3)\n"
    "elif mode == 'hang': time.sleep(3600)\n"
)


def assert_no_workload(job_id):
    unit = f"axis-acceptance@{job_id}.service"
    machine.succeed(f"test \"$(systemctl show {unit} -p ActiveState --value)\" != active")
    machine.succeed(f"test -z \"$(systemctl show {unit} -p ControlGroup --value)\" || "
                    f"! test -s /sys/fs/cgroup$(systemctl show {unit} -p ControlGroup --value)/cgroup.procs")


for mode in ("ok", "fail", "hang"):
    with subtest(f"no process survives: {mode}"):
        job = f"cleanup-{mode}-01"
        stage(job, LINGER, [mode])
        seal_and_run(job)
        assert_no_workload(job)

with subtest("no process survives: cancellation"):
    stage("cleanup-cancel-01", LINGER, ["hang"])
    machine.succeed(f"{AS_AXIS} systemctl start axis-acceptance-seal@cleanup-cancel-01.service")
    machine.succeed(f"{AS_AXIS} systemctl start --no-block axis-acceptance@cleanup-cancel-01.service")
    machine.sleep(2)
    machine.succeed(f"{AS_AXIS} systemctl stop axis-acceptance@cleanup-cancel-01.service")
    assert_no_workload("cleanup-cancel-01")

with subtest("no process survives: controller interrupted"):
    stage("cleanup-ctl-01", LINGER, ["hang"])
    machine.succeed(f"{AS_AXIS} systemctl start axis-acceptance-seal@cleanup-ctl-01.service")
    machine.succeed(f"{AS_AXIS} systemctl start --no-block axis-acceptance@cleanup-ctl-01.service")
    machine.sleep(2)
    machine.succeed("pkill -KILL -u axis || true")
    machine.wait_until_succeeds(
        "test \"$(systemctl show axis-acceptance@cleanup-ctl-01.service -p ActiveState --value)\" != active",
        timeout=60,
    )
    assert_no_workload("cleanup-ctl-01")

# ---- E. Fail closed ----------------------------------------------------------------------
with subtest("an edited template is visible to AXIS's live-property check"):
    want = machine.succeed("systemctl show axis-acceptance@probe00001.service -p ProtectSystem --value").strip()
    assert want == "strict", want
    tasks_before = machine.succeed("systemctl show axis-acceptance@probe00001.service -p TasksMax --value").strip()
    assert tasks_before == "32", tasks_before
    # /etc is read-only in the NixOS test image. Drop-in under /run.
    # Note: on this systemd, ProtectSystem=false in a drop-in appears in `systemctl cat`
    # but `systemctl show -p ProtectSystem` can remain "strict". TasksMax DOES change via
    # show — use that as the live-property visibility signal AXIS can rely on.
    machine.succeed("mkdir -p /run/systemd/system/axis-acceptance@.service.d")
    machine.succeed(
        "printf '[Service]\nProtectSystem=false\nTasksMax=1\n' > "
        "/run/systemd/system/axis-acceptance@.service.d/zz-weaken.conf"
    )
    machine.succeed("systemctl daemon-reload")
    drops = machine.succeed("systemctl show axis-acceptance@probe00001.service -p DropInPaths --value").strip()
    print(f"MEASURED DropInPaths={drops}")
    assert "zz-weaken.conf" in drops, drops
    got_ps = machine.succeed("systemctl show axis-acceptance@probe00001.service -p ProtectSystem --value").strip()
    got_tm = machine.succeed("systemctl show axis-acceptance@probe00001.service -p TasksMax --value").strip()
    print(f"MEASURED ProtectSystem after drop-in: {got_ps}")
    print(f"MEASURED TasksMax after drop-in: {got_tm}")
    assert got_tm == "1", f"TasksMax weaken not visible via show: {got_tm!r}"
    machine.succeed("rm -rf /run/systemd/system/axis-acceptance@.service.d && systemctl daemon-reload")

with subtest("the contract file is root-owned and not writable by axis"):
    machine.succeed("test \"$(stat -Lc %U /etc/axis/acceptance-runner.json)\" = root")
    machine.fail(f"{AS_AXIS} sh -c 'echo x >> /etc/axis/acceptance-runner.json'")

# ---- F. Hostile argv ---------------------------------------------------------------------
with subtest("hostile module names and job ids are rejected by the entry stub"):
    stage("inject-probe-01", "print('x')", ["$(id)", "; rm -rf /"])
    rc = seal_and_run("inject-probe-01")
    out = result("inject-probe-01")
    assert "uid=" not in out, out
    machine.fail(f"{AS_AXIS} systemctl start 'axis-acceptance@..-x.service'")

# =============================================================================
# G. Cos/Chris sealing-regression matrix (EVERY SEALING-COVERAGE.md row)
# =============================================================================

# G1 Writable descriptors held across seal
with subtest("G1 writable FD held across seal cannot mutate sealed bytes"):
    # invariant: open writable FD taken before seal must not change published snapshot bytes
    # Stage a helper script to avoid nested-quote/session issues.
    job = "fd-across-01"
    stage(job, "print('FD_BASE')", [])
    base = f"{SPOOL}/incoming/{job}/snapshot/src/probe_mod.py"
    helper = (
        "#!/bin/sh\n"
        f"exec 3>>{base}\n"
        "echo $$ > /tmp/fd-holder.pid\n"
        "while [ ! -f /tmp/fd-seal-done ]; do sleep 0.05; done\n"
        "echo FD_MUTATION >&3 2>/tmp/fd-write-err || echo FD_WRITE_FAILED > /tmp/fd-write-status\n"
        "exec 3>&-\n"
        "echo DONE > /tmp/fd-done\n"
    )
    machine.succeed("rm -f /tmp/fd-holder.pid /tmp/fd-seal-done /tmp/fd-done /tmp/fd-write-status /tmp/fd-write-err /tmp/fd-holder.sh")
    machine.succeed(f"tee /tmp/fd-holder.sh > /dev/null << 'HEOF'\n{helper}\nHEOF")
    machine.succeed("chmod 755 /tmp/fd-holder.sh && chown axis:axis /tmp/fd-holder.sh")
    machine.succeed("su -s /bin/sh axis -c 'setsid nohup /tmp/fd-holder.sh >/tmp/fd-holder.log 2>&1 &'")
    machine.wait_until_succeeds("test -s /tmp/fd-holder.pid", timeout=30)
    machine.succeed(f"{AS_AXIS} systemctl start axis-acceptance-seal@{job}.service")
    machine.succeed("echo sealed > /tmp/fd-seal-done")
    machine.wait_until_succeeds("test -f /tmp/fd-done", timeout=30)
    content = machine.succeed(f"cat {SPOOL}/jobs/{job}/snapshot/src/probe_mod.py")
    assert "FD_MUTATION" not in content, content
    assert "FD_BASE" in content, content
    sealed = machine.succeed(f"cat {SPOOL}/jobs/{job}/SEALED").strip()
    assert len(sealed) == 64, sealed
    print("G1_PASS", sealed, "write_status=", machine.succeed("cat /tmp/fd-write-status 2>/dev/null || echo none").strip())

with subtest("G2 hard links into staged tree are rejected at seal"):
    # invariant: hard-linked inodes cannot share sealed tree with axis-owned names
    # production path: seal _validate_tree rejects nlink>1
    job = "hardlink-01"
    stage(job, "print('HL')", [])
    machine.succeed(f"{AS_AXIS} ln {SPOOL}/incoming/{job}/snapshot/src/probe_mod.py /tmp/hl-outside-{job}")
    rc = machine.execute(f"{AS_AXIS} systemctl start axis-acceptance-seal@{job}.service")[0]
    assert rc != 0, "seal must fail closed on hard link"
    machine.succeed(f"test ! -e {SPOOL}/jobs/{job}")
    # partial may exist after failed validate — must not be executable as job id
    machine.fail(f"{AS_AXIS} systemctl start --wait axis-acceptance@{job}.service")
    machine.succeed(f"rm -rf {SPOOL}/jobs/{job} {SPOOL}/jobs/{job}.sealing {SPOOL}/jobs/{job}.publishing {SPOOL}/incoming/{job} /tmp/hl-outside-{job}")
    print("G2_PASS seal_rc=", rc)

# G3 Symlinks into/out of sealed tree
with subtest("G3 symlinks in staged tree are rejected at seal"):
    job = "symlink-01"
    base = f"{SPOOL}/incoming/{job}"
    machine.succeed(f"{AS_AXIS} mkdir -p {base}/snapshot/src")
    machine.succeed(f"{AS_AXIS} sh -c 'echo EVIL > /tmp/symlink-escape-target'")
    machine.succeed(f"{AS_AXIS} ln -s /tmp/symlink-escape-target {base}/snapshot/src/probe_mod.py")
    case = json.dumps({"module": "probe_mod", "argv": []})
    machine.succeed(f"{AS_AXIS} sh -c 'cat > {base}/case.json' << 'JEOF'\n{case}\nJEOF")
    rc = machine.execute(f"{AS_AXIS} systemctl start axis-acceptance-seal@{job}.service")[0]
    assert rc != 0, "seal must fail closed on symlink"
    machine.succeed(f"test ! -e {SPOOL}/jobs/{job}")
    machine.fail(f"{AS_AXIS} systemctl start --wait axis-acceptance@{job}.service")
    machine.succeed(f"rm -rf {SPOOL}/jobs/{job} {SPOOL}/jobs/{job}.sealing {SPOOL}/jobs/{job}.publishing {SPOOL}/incoming/{job}")
    print("G3_PASS seal_rc=", rc)

# G4 Concurrent mutation during seal (controlled ordering via SIGSTOP)
with subtest("G4 concurrent mutation mid-seal cannot alter published digest"):
    # Controlled ordering via AXIS_SEAL_TEST_BARRIER (workdir seal hook), not timing races.
    job = "concurrent-01"
    stage(job, "print('CONCURRENT_BASE')", [])
    es = machine.succeed(f"systemctl show axis-acceptance-seal@{job}.service -p ExecStart --value")
    pys = [x for x in re.findall(r"/nix/store/[^ ;]+", es) if x.endswith(".py")]
    assert pys, es
    sealpy = pys[0]
    print("MEASURED sealpy=", sealpy)
    barrier = "/tmp/axis-seal-barrier-concurrent"
    machine.succeed(f"rm -f {barrier}.ready {barrier}.cont /tmp/conc-mut-err /tmp/conc-seal-rc /tmp/conc-seal.pid")
    machine.succeed(
        f"bash -c 'AXIS_SEAL_TEST_BARRIER={barrier} python3 -E -s -B {sealpy} seal {job} "
        f">/tmp/conc-seal-out 2>/tmp/conc-seal-err & echo $! > /tmp/conc-seal.pid'"
    )
    machine.wait_until_succeeds(f"test -f {barrier}.ready", timeout=60)
    machine.succeed(f"test -d {SPOOL}/jobs/{job}.sealing")
    machine.succeed(f"test ! -d {SPOOL}/jobs/{job}")
    mut_rc = machine.execute(
        f"{AS_AXIS} sh -c 'echo CONCURRENT_MUTATION >> {SPOOL}/jobs/{job}.sealing/snapshot/src/probe_mod.py'"
    )[0]
    print(f"MEASURED concurrent_mut_rc={mut_rc}")
    assert mut_rc != 0, "axis mutation mid-seal must be denied after immediate lock"
    machine.succeed(f"echo cont > {barrier}.cont")
    machine.wait_until_succeeds("! kill -0 $(cat /tmp/conc-seal.pid) 2>/dev/null", timeout=60)
    machine.sleep(1)
    machine.succeed(f"test -d {SPOOL}/jobs/{job}")
    content = machine.succeed(f"cat {SPOOL}/jobs/{job}/snapshot/src/probe_mod.py")
    assert "CONCURRENT_MUTATION" not in content, content
    assert "CONCURRENT_BASE" in content, content
    print("G4_PASS published_clean mut_rc=", mut_rc)
    machine.succeed(f"rm -rf {SPOOL}/jobs/{job} {SPOOL}/jobs/{job}.sealing {SPOOL}/jobs/{job}.publishing {SPOOL}/incoming/{job}")

with subtest("G5 interrupted sealing leaves non-executable partial"):
    job = "interrupt-01"
    stage(job, "print('INTERRUPT_BASE')", [])
    es = machine.succeed(f"systemctl show axis-acceptance-seal@{job}.service -p ExecStart --value")
    pys = [x for x in re.findall(r"/nix/store/[^ ;]+", es) if x.endswith(".py")]
    assert pys, es
    sealpy = pys[0]
    barrier = "/tmp/axis-seal-barrier-interrupt"
    machine.succeed(f"rm -f {barrier}.ready {barrier}.cont /tmp/int-seal.pid")
    machine.succeed(
        f"bash -c 'AXIS_SEAL_TEST_BARRIER={barrier} python3 -E -s -B {sealpy} seal {job} "
        f">/tmp/int-seal-out 2>/tmp/int-seal-err & echo $! > /tmp/int-seal.pid'"
    )
    machine.wait_until_succeeds(f"test -f {barrier}.ready", timeout=60)
    machine.succeed(f"test -d {SPOOL}/jobs/{job}.sealing")
    machine.succeed("kill -KILL $(cat /tmp/int-seal.pid)")
    machine.succeed("echo KILLED > /tmp/int-killed")
    machine.succeed(f"test ! -d {SPOOL}/jobs/{job}")
    rc = machine.execute(f"{AS_AXIS} systemctl start --wait axis-acceptance@{job}.service")[0]
    assert rc != 0, "partial must not execute"
    print("G5_PASS non_executable rc=", rc)
    machine.succeed(f"rm -rf {SPOOL}/jobs/{job} {SPOOL}/jobs/{job}.sealing {SPOOL}/jobs/{job}.publishing {SPOOL}/incoming/{job}")

# G5b Interrupted DURING publication (staging copy done, before atomic rename)
with subtest("G5b interrupt during publication leaves non-executable partial"):
    # invariant: jobs/<id>.publishing with SEALED must not authorize; runner sees no jobs/<id>
    job = "interrupt-pub-01"
    stage(job, "print('INTERRUPT_PUB')", [])
    es = machine.succeed(f"systemctl show axis-acceptance-seal@{job}.service -p ExecStart --value")
    pys = [x for x in re.findall(r"/nix/store/[^ ;]+", es) if x.endswith(".py")]
    assert pys, es
    sealpy = pys[0]
    barrier = "/tmp/axis-seal-barrier-publish"
    machine.succeed(f"rm -f {barrier}.ready {barrier}.cont /tmp/intpub-seal.pid")
    machine.succeed(
        f"bash -c 'AXIS_SEAL_TEST_PUBLISH_BARRIER={barrier} python3 -E -s -B {sealpy} seal {job} "
        f">/tmp/intpub-seal-out 2>/tmp/intpub-seal-err & echo $! > /tmp/intpub-seal.pid'"
    )
    machine.wait_until_succeeds(f"test -f {barrier}.ready", timeout=60)
    machine.succeed(f"test -d {SPOOL}/jobs/{job}.publishing")
    machine.succeed(f"test -f {SPOOL}/jobs/{job}.publishing/SEALED")
    machine.succeed(f"test ! -d {SPOOL}/jobs/{job}")
    machine.succeed("kill -KILL $(cat /tmp/intpub-seal.pid)")
    machine.succeed(f"test ! -d {SPOOL}/jobs/{job}")
    # Real runner against partial publication state — must not execute.
    rc = machine.execute(f"{AS_AXIS} systemctl start --wait axis-acceptance@{job}.service")[0]
    assert rc != 0, "publishing staging must not authorize execution"
    # Even if SEALED exists under .publishing, entry path is jobs/<id> only.
    machine.succeed(f"test -d {SPOOL}/jobs/{job}.publishing")
    print("G5b_PASS publish_interrupt_non_executable rc=", rc)
    machine.succeed(
        f"rm -rf {SPOOL}/jobs/{job} {SPOOL}/jobs/{job}.sealing {SPOOL}/jobs/{job}.publishing {SPOOL}/incoming/{job}"
    )

# G7 Bind published bytes: controlled divergence after candidate digest / during publish
with subtest("G7 retained writable access in publish interval cannot diverge digest"):
    # Controlled-order: mutate staging bytes after SEALED candidate digest, before rename.
    # Seal must fail closed; jobs/<id> must not appear; runner must not execute.
    job = "bind-bytes-01"
    stage(job, "print('BIND_BASE')", [])
    es = machine.succeed(f"systemctl show axis-acceptance-seal@{job}.service -p ExecStart --value")
    pys = [x for x in re.findall(r"/nix/store/[^ ;]+", es) if x.endswith(".py")]
    assert pys, es
    sealpy = pys[0]
    machine.succeed("rm -f /tmp/bind-seal-out /tmp/bind-seal-err /tmp/bind-seal-rc /tmp/bind-seal.sh")
    # Write helper via printf lines to avoid nested-quote issues in the driver.
    machine.succeed(
        "printf '%s\n' "
        "'#!/bin/sh' "
        "'export AXIS_SEAL_TEST_MUTATE_PUBLISH=BIND_MUTATION' "
        f"'python3 -E -s -B {sealpy} seal {job} >/tmp/bind-seal-out 2>/tmp/bind-seal-err' "
        "'echo $? > /tmp/bind-seal-rc' "
        "> /tmp/bind-seal.sh"
    )
    machine.succeed("chmod 755 /tmp/bind-seal.sh && /tmp/bind-seal.sh")
    seal_rc = int(machine.succeed("cat /tmp/bind-seal-rc").strip())
    assert seal_rc != 0, "seal must fail when published bytes diverge from SEALED digest"
    machine.succeed(f"test ! -d {SPOOL}/jobs/{job}")
    err = machine.succeed("cat /tmp/bind-seal-err || true")
    assert "published digest mismatch" in err, err
    rc_run = machine.execute(f"{AS_AXIS} systemctl start --wait axis-acceptance@{job}.service")[0]
    assert rc_run != 0, "diverged publish must not authorize execution"
    print("G7_PASS bind_published_bytes seal_rc=", seal_rc, "run_rc=", rc_run)
    machine.succeed(
        f"rm -rf {SPOOL}/jobs/{job} {SPOOL}/jobs/{job}.sealing {SPOOL}/jobs/{job}.publishing {SPOOL}/incoming/{job}"
    )

# G8 Entry rejects published snapshot that does not match SEALED
with subtest("G8 entry verifies published snapshot against SEALED before exec"):
    job = "entry-bind-01"
    stage(job, "print('ENTRY_BIND_OK')", [])
    machine.succeed(f"{AS_AXIS} systemctl start axis-acceptance-seal@{job}.service")
    sealed = machine.succeed(f"cat {SPOOL}/jobs/{job}/SEALED").strip()
    # Tamper published snapshot as root after seal (simulates divergent published bytes).
    machine.succeed(
        f"chmod u+w {SPOOL}/jobs/{job}/snapshot/src/probe_mod.py && "
        f"echo TAMPERED >> {SPOOL}/jobs/{job}/snapshot/src/probe_mod.py && "
        f"chmod 444 {SPOOL}/jobs/{job}/snapshot/src/probe_mod.py"
    )
    rc = machine.execute(f"{AS_AXIS} systemctl start --wait axis-acceptance@{job}.service")[0]
    assert rc != 0, "entry must DENY exec when published snapshot != SEALED"
    # Restore original bytes so digest matches SEALED again.
    machine.succeed(f"chmod u+w {SPOOL}/jobs/{job}/snapshot/src/probe_mod.py")
    machine.succeed(
        f"tee {SPOOL}/jobs/{job}/snapshot/src/probe_mod.py > /dev/null << 'PYEOF'\nprint('ENTRY_BIND_OK')\nPYEOF"
    )
    machine.succeed(f"chmod 444 {SPOOL}/jobs/{job}/snapshot/src/probe_mod.py")
    rc_ok = machine.execute(f"{AS_AXIS} systemctl start --wait axis-acceptance@{job}.service")[0]
    assert rc_ok == 0, rc_ok
    out = result(job)
    assert "ENTRY_BIND_OK" in out, out
    print("G8_PASS entry_bind sealed=", sealed, "deny_rc=", rc, "ok_rc=", rc_ok)
    machine.succeed(f"rm -rf {SPOOL}/jobs/{job} {SPOOL}/jobs/{job}.sealing {SPOOL}/jobs/{job}.publishing")

with subtest("G6 repeated seal/start cannot substitute different content"):
    # RemainAfterExit=true: second systemctl start may no-op (rc=0) without re-running
    # ExecStart. That is OK iff digest+bytes stay CONTENT_A. Direct seal.py re-seal and
    # substitute-content seal must return non-zero (fail-closed).
    job = "repeat-req-01"
    stage(job, "print('CONTENT_A')", [])
    machine.succeed(f"{AS_AXIS} systemctl start axis-acceptance-seal@{job}.service")
    sealed_a = machine.succeed(f"cat {SPOOL}/jobs/{job}/SEALED").strip()
    rc2 = machine.execute(f"{AS_AXIS} systemctl start axis-acceptance-seal@{job}.service")[0]
    print(f"MEASURED second_systemctl_start_rc={rc2}")
    sealed_mid = machine.succeed(f"cat {SPOOL}/jobs/{job}/SEALED").strip()
    assert sealed_a == sealed_mid, (sealed_a, sealed_mid)
    content = machine.succeed(f"cat {SPOOL}/jobs/{job}/snapshot/src/probe_mod.py")
    assert "CONTENT_A" in content, content
    es = machine.succeed(f"systemctl show axis-acceptance-seal@{job}.service -p ExecStart --value")
    pys = [x for x in re.findall(r"/nix/store/[^ ;]+", es) if x.endswith(".py")]
    assert pys, es
    sealpy = pys[0]
    rc_direct = machine.execute(f"python3 -E -s -B {sealpy} seal {job}")[0]
    assert rc_direct != 0, "direct re-seal must fail while published exists"
    machine.execute(f"{AS_AXIS} mkdir -p {SPOOL}/incoming/{job}/snapshot/src")
    machine.execute(
        f"{AS_AXIS} tee {SPOOL}/incoming/{job}/snapshot/src/probe_mod.py > /dev/null << 'PYB'\nprint('CONTENT_B')\nPYB"
    )
    rc3 = machine.execute(f"python3 -E -s -B {sealpy} seal {job}")[0]
    assert rc3 != 0, "seal must refuse substitute content while published exists"
    content = machine.succeed(f"cat {SPOOL}/jobs/{job}/snapshot/src/probe_mod.py")
    assert "CONTENT_A" in content, content
    assert "CONTENT_B" not in content, content
    sealed_b = machine.succeed(f"cat {SPOOL}/jobs/{job}/SEALED").strip()
    assert sealed_a == sealed_b, (sealed_a, sealed_b)
    machine.succeed(f"{AS_AXIS} systemctl start --wait axis-acceptance@{job}.service")
    out = result(job)
    assert "CONTENT_A" in out, out
    rc_start2 = machine.execute(f"{AS_AXIS} systemctl start --wait axis-acceptance@{job}.service")[0]
    print("G6_PASS sealed=", sealed_a, "second_systemctl_rc=", rc2, "direct_reseal_rc=", rc_direct, "subst_rc=", rc3, "repeat_start_rc=", rc_start2)

print("SEALING_MATRIX_COMPLETE")
