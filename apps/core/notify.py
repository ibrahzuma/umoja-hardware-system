"""Helper to raise a per-user notification (inbox item).

Usage:
    from apps.core.notify import notify
    notify(user, "Title", "Message body", url="/inventory/...", level="warning")
"""


def notify(recipient, title, message="", url="", level="info"):
    """Create a Notification for `recipient`. Safe to call in request flow;
    returns the created object (or None if no recipient)."""
    if recipient is None:
        return None
    from .models import Notification
    return Notification.objects.create(
        recipient=recipient, title=title, message=message, url=url, level=level,
    )
