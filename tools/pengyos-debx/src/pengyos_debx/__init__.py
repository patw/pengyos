"""PengyOS debx.

Bounded deb/rpm extractor for PengyOS.

This package intentionally NEVER executes a package's maintainer scripts. It only:

* reads the ``ar``/tar deb payload or the ``cpio``/zstd rpm payload,
* extracts files to an isolated install prefix,
* lists maintainer scripts for inspection, and
* analyses ``DT_NEEDED`` entries by pure parsing of ELF headers (never ``ldd``,
  which can execute).
"""

__version__ = "0.1.0"

__all__ = ["__version__"]
