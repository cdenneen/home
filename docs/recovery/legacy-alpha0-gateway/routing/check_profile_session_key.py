"""Fail unless the Alpha0 service shim preserves multiplexed clarify routing."""

from gateway.config import Platform
from gateway.platforms import base
from gateway.session import SessionSource, build_session_key
from tools import clarify_gateway


source = SessionSource(
    platform=Platform.SLACK,
    scope_id="TALPHA0",
    chat_id="DALPHA0",
    chat_type="dm",
    user_id="UALPHA0",
    thread_id="1234567890.123456",
    profile="alpha0",
)
expected = "agent:alpha0:slack:dm:TALPHA0:DALPHA0:1234567890.123456"
legacy = "agent:main:slack:dm:TALPHA0:DALPHA0:1234567890.123456"

assert build_session_key(source) == legacy
assert base.build_session_key(source) == expected

clarify_id = "alpha0-profile-session-key-check"
clarify_gateway.register(clarify_id, expected, "Synthetic question", None)
try:
    assert clarify_gateway.get_pending_for_session(expected) is not None
finally:
    clarify_gateway.clear_session(expected)
