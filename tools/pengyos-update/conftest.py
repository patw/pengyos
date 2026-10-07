"""src layout: put src (and pengyos-install's, for grub.cfg) on sys.path."""

import os
import sys

_HERE = os.path.dirname(__file__)
for _p in (os.path.join(_HERE, "src"), os.path.join(_HERE, "..", "pengyos-install", "src")):
    if _p not in sys.path:
        sys.path.insert(0, _p)
