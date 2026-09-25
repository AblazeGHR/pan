"""Stable handoff identities in canonical history API rows."""

from packages.web import server


def test_history_api_projects_delivery_receipt_keys_as_public_identity():
    projected = server._api_history("session-a", [{
        "role": "user",
        "content": "agent instruction",
        "delivered_keys": ["task:q-agent-1", "report:task-2:abc123"],
    }])

    assert projected == [{
        "role": "user",
        "content": "agent instruction",
        "deliveryKeys": ["task:q-agent-1", "report:task-2:abc123"],
        "messageId": "legacy:session-a:legacy:0",
    }]


def test_history_without_delivery_receipt_does_not_gain_one():
    projected = server._api_history("session-a", [{
        "role": "user", "content": "ordinary user message",
    }])

    assert "deliveryKeys" not in projected[0]
    assert "delivered_keys" not in projected[0]
