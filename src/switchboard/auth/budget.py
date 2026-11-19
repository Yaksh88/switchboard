from __future__ import annotations

import time
import uuid
from datetime import UTC, datetime

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from switchboard.auth.keys import Principal
from switchboard.db.models import RequestLog
from switchboard.errors import RateLimitError


def month_bounds(now: datetime) -> tuple[datetime, datetime]:
    start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    if start.month == 12:
        end = start.replace(year=start.year + 1, month=1)
    else:
        end = start.replace(month=start.month + 1)
    return start, end


class BudgetGuard:
    """Rejects requests from keys that have spent their monthly budget.

    Spend is summed from request logs and cached per key for ``cache_ttl_s``, so a key can
    overshoot its budget by whatever it spends inside one cache window.
    """

    def __init__(
        self, sessions: async_sessionmaker[AsyncSession], cache_ttl_s: float = 30.0
    ) -> None:
        self.sessions = sessions
        self.cache_ttl_s = cache_ttl_s
        self._cache: dict[uuid.UUID, tuple[float, float]] = {}

    async def month_to_date_spend(self, key_id: uuid.UUID) -> float:
        cached = self._cache.get(key_id)
        if cached is not None and cached[0] > time.monotonic():
            return cached[1]

        start, _ = month_bounds(datetime.now(UTC))
        async with self.sessions() as session:
            total = await session.scalar(
                select(func.coalesce(func.sum(RequestLog.cost_usd), 0)).where(
                    RequestLog.api_key_id == key_id, RequestLog.created_at >= start
                )
            )
        spend = float(total or 0)
        self._cache[key_id] = (time.monotonic() + self.cache_ttl_s, spend)
        return spend

    async def check(self, principal: Principal) -> None:
        budget = principal.monthly_budget_usd
        if budget is None:
            return
        if await self.month_to_date_spend(principal.key_id) < budget:
            return
        now = datetime.now(UTC)
        _, next_month = month_bounds(now)
        raise RateLimitError(
            f"Monthly budget of ${budget:g} exhausted",
            retry_after_s=(next_month - now).total_seconds(),
            code="budget_exceeded",
        )
