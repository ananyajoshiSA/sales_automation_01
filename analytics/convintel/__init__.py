"""Conversation intelligence and team analytics: an additive module (docs/conversation_intelligence.md).

Every call in the LeadSquared call log is inventoried, linked to its transcript, analysed, validated and
tracked with an explicit status until it is analysed, so no call drops out unnoticed. Nothing here changes
the existing reports, the dashboard's existing numbers or the shared definitions in analytics/definitions.py:
the 3-minute REAL_CALL rule (analytics/convintel/classify.py) applies inside this module only.

    python -m analytics.convintel status
"""
