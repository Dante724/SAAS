"""Kept for imports from WS-C; the single implementation is services/public_name.py."""
from backend.services.public_name import MODES, public_staff_name

display_name = public_staff_name

__all__ = ["MODES", "display_name", "public_staff_name"]
