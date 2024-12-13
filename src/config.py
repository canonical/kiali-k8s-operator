"""Configuration parser for the charm."""

from pydantic import BaseModel, Field


class CharmConfig(BaseModel):
    """Manager for the charm configuration."""
