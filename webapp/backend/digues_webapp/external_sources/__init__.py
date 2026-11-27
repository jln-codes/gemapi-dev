"""Consultation bornée de sources Internet explicitement autorisées."""

from .search import ExternalSourceSearchError, search_external_sources

__all__ = ["ExternalSourceSearchError", "search_external_sources"]
