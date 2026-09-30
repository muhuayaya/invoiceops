"""Verified, versioned model loading for online inference."""

from .runtime import ModelLoadError, load_model

__all__ = ["ModelLoadError", "load_model"]
