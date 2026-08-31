"""
Continuity controller: CONTINUITY-AUTO (automatic, qualified-outage-only)
and BREAK-GLASS (explicit human activation). See execution-contract.md 10.

Never-sufficient-alone signals never reach this module at all - the
endpoint layer must not even call into continuity on a 429/budget/auth/
policy/cost-integrity/capability failure. This module only reacts to
`OutageClassifier` output and explicit human BREAK-GLASS activation.
"""

import json
import os
import time
from dataclasses import dataclass
from pathlib import Path


class ContinuityDenied(Exception):
    """Raised whenever continuity is requested but cannot be safely
    activated. Callers must treat this as DENY, never as permission to
    fall back to an unrestricted direct-provider credential."""


@dataclass(frozen=True)
class EmergencyCredential:
    provider: str
    model: str
    key_or_role: str
    daily_cap_usd: float
    monthly_cap_usd: float


# G-CONT (2026-08-31): default, host-local, already-authorized reference
# used ONLY when no explicit emergency_credential_path is configured for
# this actor. This is a REFERENCE (path + var name), never a copied
# secret - the actual value is read from the existing file at the moment
# it's used (endpoint.py's _forward_continuity), never duplicated here or
# at load() time, never logged. Reuses the SAME static OPENAI_API_KEY
# Ghost's own base Hermes gateway already uses independently of Eros -
# "reuse an already-authorized independent credential" before ever
# provisioning a new one (per explicit instruction: do not clone a
# LiteLLM virtual key into the emergency store; that would preserve the
# same failure dependency this exists to route around).
_DEFAULT_REFERENCE_ENV_FILE = Path.home() / ".hermes" / ".env"
_DEFAULT_REFERENCE_ENV_VAR = "OPENAI_API_KEY"
DEFAULT_REFERENCE_KEY_OR_ROLE = f"env_file:{_DEFAULT_REFERENCE_ENV_FILE}#{_DEFAULT_REFERENCE_ENV_VAR}"

# Greptile P1 (PR #735, round 3): every actor instance on this host falls
# back to this SAME default reference credential (there is only one
# OPENAI_API_KEY on Ghost) - if two+ actors entered continuity at once,
# each checking its own per-actor state.db would under-count the
# credential's true aggregate usage. Callers must check this and, when
# true, track burn in a SHARED state store (see endpoint.py's
# _shared_default_credential_state), not the per-actor one.
def is_shared_default_credential(cred: "EmergencyCredential") -> bool:
    return cred.key_or_role == DEFAULT_REFERENCE_KEY_OR_ROLE
_DEFAULT_REFERENCE_MODEL = "gpt-5.6-sol"
# Deliberately small - this path exists to keep essential background
# work moving, not to fund normal-priced usage. Well below the actor's
# normal LiteLLM budget.
_DEFAULT_REFERENCE_DAILY_CAP_USD = 5.0
_DEFAULT_REFERENCE_MONTHLY_CAP_USD = 50.0


def _default_reference_credential() -> "EmergencyCredential | None":
    """Returns a reference-based EmergencyCredential pointing at Ghost's
    existing independent OpenAI key IF that key is actually discoverable
    right now, else None (never fabricates availability)."""
    try:
        if not _DEFAULT_REFERENCE_ENV_FILE.exists():
            return None
        with open(_DEFAULT_REFERENCE_ENV_FILE) as f:
            for line in f:
                if line.startswith(f"{_DEFAULT_REFERENCE_ENV_VAR}="):
                    break
            else:
                return None
    except OSError:
        return None
    return EmergencyCredential(
        provider="openai",
        model=_DEFAULT_REFERENCE_MODEL,
        key_or_role=DEFAULT_REFERENCE_KEY_OR_ROLE,
        daily_cap_usd=_DEFAULT_REFERENCE_DAILY_CAP_USD,
        monthly_cap_usd=_DEFAULT_REFERENCE_MONTHLY_CAP_USD,
    )


def resolve_credential_reference(key_or_role: str) -> str:
    """Resolves a `key_or_role` reference to its live secret value at call
    time. Only supports the `env_file:<path>#<VAR>` reference shape - a
    literal (non-reference) value is rejected, since accepting one would
    invite copying secrets into credential descriptors instead of
    referencing where they already live. Never logs the resolved value;
    callers must not either."""
    if not key_or_role.startswith("env_file:") or "#" not in key_or_role:
        raise ContinuityDenied(
            f"unsupported credential reference shape: {key_or_role!r} "
            "(expected env_file:<path>#<VAR>)"
        )
    path_part, var_name = key_or_role[len("env_file:"):].rsplit("#", 1)
    path = Path(path_part)
    if not path.exists():
        raise ContinuityDenied(f"credential reference file missing: {path}")
    try:
        with open(path) as f:
            for line in f:
                if line.startswith(f"{var_name}="):
                    value = line[len(var_name) + 1:].rstrip("\n")
                    if not value:
                        raise ContinuityDenied(f"credential reference {var_name} is empty in {path}")
                    return value
    except OSError as exc:
        raise ContinuityDenied(f"credential reference file unreadable: {exc}") from exc
    raise ContinuityDenied(f"credential reference var {var_name} not found in {path}")


class EmergencyCredentialStore:
    """
    Loads the emergency credential from a SOPS-provisioned path. If the
    path doesn't exist or is unreadable, falls back to the default
    host-local reference credential (see above) if one is discoverable;
    if neither is available, continuity MUST be denied
    (execution-contract.md BOOT-013) - normal credentials are never
    substituted.
    """

    def __init__(self, credential_path: Path | None):
        self.credential_path = credential_path

    def load(self) -> EmergencyCredential:
        if self.credential_path is not None and self.credential_path.exists():
            try:
                data = json.loads(self.credential_path.read_text())
            except (OSError, json.JSONDecodeError) as exc:
                raise ContinuityDenied(f"emergency credential unreadable: {exc}") from exc
            try:
                return EmergencyCredential(**data)
            except TypeError as exc:
                raise ContinuityDenied(f"emergency credential malformed: {exc}") from exc
        default = _default_reference_credential()
        if default is not None:
            return default
        raise ContinuityDenied(
            f"emergency credential not provisioned at {self.credential_path!s} "
            "and no default host-local reference credential is discoverable"
        )


class ContinuityController:
    def __init__(
        self,
        *,
        state,  # state.LocalState
        credential_store: EmergencyCredentialStore,
        break_glass_flag_path: Path,
        break_glass_max_duration_s: float = 30 * 60,
    ):
        self.state = state
        self.credential_store = credential_store
        self.break_glass_flag_path = break_glass_flag_path
        self.break_glass_max_duration_s = break_glass_max_duration_s

    # --- CONTINUITY-AUTO ----------------------------------------------------

    def activate_continuity_auto(self, evidence: dict) -> EmergencyCredential:
        """Raises ContinuityDenied if the credential isn't available - this
        is the correct, safe outcome when continuity can't actually be
        granted; it must never silently fall through to a normal key."""
        cred = self.credential_store.load()  # raises ContinuityDenied if absent
        open_ep = self.state.open_continuity_episode()
        if open_ep is None:
            self.state.start_continuity_episode(
                mode="continuity_auto", reason="qualified_eros_outage", evidence=evidence
            )
        return cred

    # --- BREAK-GLASS ---------------------------------------------------------

    def break_glass_activate(self, reason: str) -> None:
        """Explicit human activation. Bootstrap implementation: an
        operator-created flag file with an expiry, checked on every
        request. This is intentionally simple and auditable rather than
        a network-exposed control endpoint."""
        self.break_glass_flag_path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        payload = {"reason": reason, "activated_at": time.time()}
        self.break_glass_flag_path.write_text(json.dumps(payload))
        self.break_glass_flag_path.chmod(0o600)
        self.state.start_continuity_episode(
            mode="break_glass", reason=reason, evidence={"human_activated": True}
        )

    def break_glass_active(self) -> bool:
        if not self.break_glass_flag_path.exists():
            return False
        try:
            payload = json.loads(self.break_glass_flag_path.read_text())
        except (OSError, json.JSONDecodeError):
            # Corrupt flag file is not activation evidence - fail closed.
            return False
        age = time.time() - payload.get("activated_at", 0)
        if age > self.break_glass_max_duration_s:
            self.break_glass_deactivate()
            return False
        return True

    def break_glass_deactivate(self) -> None:
        self.break_glass_flag_path.unlink(missing_ok=True)
        open_ep = self.state.open_continuity_episode()
        if open_ep is not None and open_ep["mode"] == "break_glass":
            self.state.end_continuity_episode(open_ep["id"])

    # --- recovery / reconciliation --------------------------------------------

    def end_continuity_auto_if_recovered(self):
        open_ep = self.state.open_continuity_episode()
        if open_ep is not None and open_ep["mode"] == "continuity_auto":
            self.state.end_continuity_episode(open_ep["id"])

    def pending_reconciliation(self):
        """Continuity episodes that ended but haven't yet been folded back
        into the EPR pipeline (execution-contract.md 11: local continuity
        accounting joins the EPR after recovery)."""
        return self.state.unreconciled_episodes()
