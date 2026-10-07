"""PengyOS appimage.

Inspect, extract, integrate, and sandbox AppImages under PengyOS.

This package intentionally NEVER executes an AppImage's embedded hooks. It only
reads static metadata, extracts files to a prefix, generates desktop/mime
integration, and launches the extracted application under a bubblewrap sandbox.
"""

__version__ = "0.1.0"

__all__ = ["__version__"]
