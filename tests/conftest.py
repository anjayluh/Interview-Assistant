from datetime import date

import pytest

from app import cost


@pytest.fixture(autouse=True)
def pricing_clock(monkeypatch):
    """The audited pricing snapshot must expire in production, not in tests."""
    class VerificationDay(date):
        @classmethod
        def today(cls):
            return cost.PRICING_DATE

    monkeypatch.setattr(cost, 'date', VerificationDay)
