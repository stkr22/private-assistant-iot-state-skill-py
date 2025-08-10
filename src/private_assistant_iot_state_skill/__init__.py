"""This package allows the querying of iot states via timescaledb."""

try:
    from ._version import __version__
except ImportError:
    # Fallback for development installs
    __version__ = "dev"

__all__ = ["__version__"]