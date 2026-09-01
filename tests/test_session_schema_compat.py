"""Session persistence compatibility across Pan version rollbacks."""

from packages.core.session import Session


def test_loads_session_written_by_newer_queue_version():
    """New queue bookkeeping must not prevent the rolled-back server booting."""
    data = {
        "id": "ses_forward_compat",
        "name": "forward-compat",
        "queue_pending": [{"type": "task", "text": "still pending"}],
        "queue_delivery_ledger": {
            "q_example": {"deliveryState": "sent_to_cli"},
        },
        "queue_revision": 7,
    }

    session = Session._from_data(data)

    assert session.queue_pending == [{"type": "task", "text": "still pending"}]
    assert session.id == "ses_forward_compat"
    assert "queue_delivery_ledger" not in session.to_dict()
    assert "queue_revision" not in session.to_dict()
