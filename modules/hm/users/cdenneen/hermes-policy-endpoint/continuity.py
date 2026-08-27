"""
Continuity controller: CONTINUITY-AUTO (automatic, qualified-outage-only)
and BREAK-GLASS (explicit human activation). See execution-contract.md 10.

Never-sufficient-alone signals never reach this module at all - the
endpoint layer must not even call into continuity on a 429/budget/auth/
policy/cost-integrity/capability failure. This module only reacts to
`OutageClassifier` output and explicit human BREAK-GLASS activation.
"""

import json
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


class EmergencyCredentialStore:
    """
    Loads the emergency credential from a SOPS-provisioned path. If the
    path doesn't exist or is unreadable, continuity MUST be denied
    (execution-contract.md BOOT-013) - normal credentials are never
    substituted.

    No emergency credential has been provisioned yet on either host as of
    this writing (new OpenAI keys are console-only; a new restricted AWS
    IAM role is a separate, explicitly-authorized action). This class's
    "not provisioned" path is exactly what BOOT-013 exercises today.
    """

    def __init__(self, credential_path: Path | None):
        self.credential_path = credential_path

    def load(self) -> EmergencyCredential:
        if self.credential_path is None or not self.credential_path.exists():
            raise ContinuityDenied(
                f"emergency credential not provisioned at "
                f"{self.credential_path!s}"
            )
        try:
            data = json.loads(self.credential_path.read_text())
        except (OSError, json.JSONDecodeError) as exc:
            raise ContinuityDenied(f"emergency credential unreadable: {exc}") from exc
        try:
            return EmergencyCredential(**data)
        except TypeError as exc:
            raise ContinuityDenied(f"emergency credential malformed: {exc}") from exc


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
