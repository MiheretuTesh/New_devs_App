from fastapi import APIRouter, Depends, HTTPException, Query
from typing import Dict, Any, Optional
from app.services.cache import get_revenue_summary
from app.services.reservations import calculate_monthly_revenue, list_properties
from app.core.auth import authenticate_request as get_current_user

router = APIRouter()


def _require_tenant(current_user) -> str:
    """Resolve the caller's tenant, refusing the request if there isn't one.

    The previous default of "default_tenant" meant an unresolved tenant silently
    became a real-looking scope key instead of failing closed.
    """
    tenant_id = getattr(current_user, "tenant_id", None)
    if not tenant_id:
        raise HTTPException(status_code=403, detail="No tenant associated with this user")
    return tenant_id


@router.get("/dashboard/properties")
async def get_dashboard_properties(
    current_user: dict = Depends(get_current_user)
) -> Dict[str, Any]:
    # Only the caller's own properties, so the selector can't show another tenant's names.
    tenant_id = _require_tenant(current_user)
    return {"properties": await list_properties(tenant_id)}


@router.get("/dashboard/summary")
async def get_dashboard_summary(
    property_id: str,
    month: Optional[int] = Query(None, ge=1, le=12),
    year: Optional[int] = Query(None, ge=2000, le=2100),
    current_user: dict = Depends(get_current_user)
) -> Dict[str, Any]:

    tenant_id = _require_tenant(current_user)

    if (month is None) != (year is None):
        raise HTTPException(status_code=400, detail="month and year must be supplied together")

    if month is not None:
        # Month totals are bounded by the property's local timezone.
        try:
            revenue_data = await calculate_monthly_revenue(property_id, tenant_id, month, year)
        except LookupError:
            # The property does not exist *for this tenant*. Reported the same way as a
            # genuinely missing property, so the response cannot be used to probe which
            # property ids other tenants own.
            raise HTTPException(status_code=404, detail="Property not found")
    else:
        revenue_data = await get_revenue_summary(property_id, tenant_id)

    # total_revenue is returned as an exact decimal *string*. Serialising it as a
    # JSON number would re-introduce binary floating point at the API boundary and
    # reproduce the few-cents drift the finance team reported.
    response: Dict[str, Any] = {
        "property_id": revenue_data["property_id"],
        "total_revenue": revenue_data["total"],
        "currency": revenue_data["currency"],
        "reservations_count": revenue_data["count"],
    }

    if month is not None:
        response.update({
            "month": revenue_data["month"],
            "year": revenue_data["year"],
            "timezone": revenue_data["timezone"],
            "period_start": revenue_data["period_start"],
            "period_end": revenue_data["period_end"],
        })

    return response
