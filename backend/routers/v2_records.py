"""API v2: Lookups (locations, employees), contact search, order updates and status, order lines, invoice detail.

Routes register on backend.routers.v2.router, so they share its auth, limits and OpenAPI.
"""
from backend.routers.v2 import router  # noqa: F401
