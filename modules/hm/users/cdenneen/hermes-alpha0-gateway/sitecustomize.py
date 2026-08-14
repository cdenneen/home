"""Keep Hermes adapter session keys aligned with multiplexed profile routing."""

try:
    import gateway.platforms.base as _base

    _original_build_session_key = _base.build_session_key

    def _profile_aware_build_session_key(source, *args, **kwargs):
        # Hermes' adapter omits profile while its runner includes it. Preserve
        # explicit caller choices and legacy unprofiled behavior, but default
        # an omitted profile to the route already attached to the source.
        if len(args) < 3:
            kwargs.setdefault("profile", getattr(source, "profile", None))
        return _original_build_session_key(source, *args, **kwargs)

    _base.build_session_key = _profile_aware_build_session_key
except Exception:
    # The systemd preflight verifies this patch before starting the gateway.
    pass
