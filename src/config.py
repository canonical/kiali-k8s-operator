"""Configuration parser for the charm."""

from pydantic import BaseModel


class CharmConfig(BaseModel):
    """Manager for the charm configuration."""
