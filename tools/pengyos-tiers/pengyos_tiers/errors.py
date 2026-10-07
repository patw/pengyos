"""Shared error types for pengyos-tiers."""

from __future__ import annotations


class PengyOSTiersError(Exception):
    """Base class for all pengyos-tiers errors."""


class ChangeError(PengyOSTiersError):
    """A proposed change failed schema validation."""


class RulesError(PengyOSTiersError):
    """The declarative rules file could not be parsed or is invalid."""
