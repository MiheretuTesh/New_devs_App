from datetime import datetime
from decimal import Decimal, ROUND_HALF_UP
from typing import Dict, Any, List
from zoneinfo import ZoneInfo

from sqlalchemy import text

from app.core.database_pool import db_pool

# Money is stored as NUMERIC(10, 3) so sub-cent amounts survive in the database.
# Everything that leaves this service is summed exactly as Decimal and only then
# rounded once, to cents. Rounding per-row, or routing the value through a binary
# float, is what produced the "off by a few cents" totals.
CENTS = Decimal("0.01")


def _to_cents(amount: Decimal) -> Decimal:
    """Round an exact Decimal total to 2 dp, half-up (the banking convention here)."""
    return Decimal(amount).quantize(CENTS, rounding=ROUND_HALF_UP)


def _month_bounds(year: int, month: int, tz: ZoneInfo) -> tuple[datetime, datetime]:
    """Return [start, end) for a calendar month *in the property's local timezone*.

    The reservation timestamps are TIMESTAMPTZ (absolute instants). A month has to
    be bounded by the local midnights of that property, otherwise a stay that
    begins at 2024-03-01 00:30 in Europe/Paris - stored as 2024-02-29 23:30Z - is
    counted in February and the month totals disagree with the client's own books.
    """
    start = datetime(year, month, 1, tzinfo=tz)
    if month == 12:
        end = datetime(year + 1, 1, 1, tzinfo=tz)
    else:
        end = datetime(year, month + 1, 1, tzinfo=tz)
    return start, end


async def _property_timezone(session, property_id: str, tenant_id: str) -> ZoneInfo:
    """Look up a property's timezone, scoped to the tenant that owns it."""
    result = await session.execute(
        text(
            """
            SELECT timezone
            FROM properties
            WHERE id = :property_id AND tenant_id = :tenant_id
            """
        ),
        {"property_id": property_id, "tenant_id": tenant_id},
    )
    row = result.fetchone()
    if row is None:
        raise LookupError(f"Property {property_id} not found for tenant {tenant_id}")
    try:
        return ZoneInfo(row.timezone or "UTC")
    except Exception:
        return ZoneInfo("UTC")


async def calculate_monthly_revenue(
    property_id: str,
    tenant_id: str,
    month: int,
    year: int,
) -> Dict[str, Any]:
    """
    Calculates revenue for a specific month, bounded by the property's local timezone.
    """
    if not tenant_id:
        raise ValueError("tenant_id is required: revenue must never be queried across tenants")

    await db_pool.initialize()

    async with db_pool.get_session() as session:
        tz = await _property_timezone(session, property_id, tenant_id)
        start_date, end_date = _month_bounds(year, month, tz)

        # tenant_id is part of the predicate, not just property_id: property ids are
        # only unique within a tenant (composite PK on (id, tenant_id)).
        query = text(
            """
            SELECT
                COALESCE(SUM(total_amount), 0) AS total_revenue,
                COUNT(*) AS reservation_count
            FROM reservations
            WHERE property_id = :property_id
              AND tenant_id = :tenant_id
              AND check_in_date >= :start_date
              AND check_in_date < :end_date
            """
        )

        result = await session.execute(
            query,
            {
                "property_id": property_id,
                "tenant_id": tenant_id,
                "start_date": start_date,
                "end_date": end_date,
            },
        )
        row = result.fetchone()

    exact_total = Decimal(str(row.total_revenue))

    return {
        "property_id": property_id,
        "tenant_id": tenant_id,
        "year": year,
        "month": month,
        "timezone": str(tz),
        "period_start": start_date.isoformat(),
        "period_end": end_date.isoformat(),
        "total": str(_to_cents(exact_total)),
        "total_exact": str(exact_total),
        "currency": "USD",
        "count": row.reservation_count,
    }


async def calculate_total_revenue(property_id: str, tenant_id: str) -> Dict[str, Any]:
    """
    Aggregates revenue from database.
    """
    if not tenant_id:
        raise ValueError("tenant_id is required: revenue must never be queried across tenants")

    await db_pool.initialize()

    async with db_pool.get_session() as session:
        # SUM is computed by Postgres over NUMERIC, so the total is exact before it
        # ever reaches Python. Summing floats here would drift.
        query = text(
            """
            SELECT
                COALESCE(SUM(total_amount), 0) AS total_revenue,
                COUNT(*) AS reservation_count
            FROM reservations
            WHERE property_id = :property_id AND tenant_id = :tenant_id
            """
        )

        result = await session.execute(
            query,
            {"property_id": property_id, "tenant_id": tenant_id},
        )
        row = result.fetchone()

    exact_total = Decimal(str(row.total_revenue))

    # Previously a failure here was swallowed and replaced with hardcoded per-property
    # mock figures, which were identical for every tenant and impossible to tell apart
    # from real data. An outage must surface as an error, never as plausible numbers.
    return {
        "property_id": property_id,
        "tenant_id": tenant_id,
        "total": str(_to_cents(exact_total)),
        "total_exact": str(exact_total),
        "currency": "USD",
        "count": row.reservation_count,
    }


async def list_properties(tenant_id: str) -> List[Dict[str, str]]:
    """
    Lists the properties that belong to a tenant, for the dashboard selector.
    """
    if not tenant_id:
        raise ValueError("tenant_id is required: properties must never be listed across tenants")

    await db_pool.initialize()

    async with db_pool.get_session() as session:
        result = await session.execute(
            text(
                """
                SELECT id, name, timezone
                FROM properties
                WHERE tenant_id = :tenant_id
                ORDER BY id
                """
            ),
            {"tenant_id": tenant_id},
        )
        return [
            {"id": row.id, "name": row.name, "timezone": row.timezone}
            for row in result.fetchall()
        ]
