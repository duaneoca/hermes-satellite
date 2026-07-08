"""On-demand setup wizard: an ephemeral, token-protected web UI.

Security model (docs/networking.md, docs/setup-wizard.md): the satellite
runs **no** resident web server. ``hermes-satellite setup`` starts this one
temporarily — a random one-time token gates every request (which also
defeats CSRF: a hostile page can't know the token), the process exits after
an idle timeout or the Exit button, and while it runs the daemon should be
stopped (they share the microphone).
"""

from .server import run_wizard  # noqa: F401
