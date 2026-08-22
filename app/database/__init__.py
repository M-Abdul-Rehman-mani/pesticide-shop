"""Database package.

Engine construction is intentionally not imported here: importing model metadata must
not create a network-capable engine or require runtime environment variables.
"""

from app.database.base import Base

__all__ = ["Base"]
