from __future__ import annotations


class UserError(RuntimeError):
    """A problem the user can fix; the message is shown as is in the dashboard."""
