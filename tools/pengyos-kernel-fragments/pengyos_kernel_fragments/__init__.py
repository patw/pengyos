"""PengyOS kernel-fragments — kernel config fragment library + composer.

A small, standard-library-only toolkit for composing reproducible Linux
kernel config fragments. Fragments live in a directory, profiles reference a
list of them, and the tool detects value conflicts and prints the merged
``CONFIG`` set.
"""

__version__ = "0.1.0"
