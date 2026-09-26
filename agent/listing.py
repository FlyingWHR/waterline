"""Listing reading lives in core/listing.py (shared with the API, which bundles core/); this name is the same module."""
import sys

from core import listing as _listing

sys.modules[__name__] = _listing
