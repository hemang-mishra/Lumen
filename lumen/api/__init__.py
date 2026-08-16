"""
The web surface.

One way in for anything outside the process: today, read-only access to the
knowledge graph and to the history of the runs that built it.

`create_app` builds the application. It is a function rather than a
module-level object so that naming this package does not open a database,
and so a test can point the whole thing at temporary ones.
"""

from lumen.api.main import create_app

__all__ = ["create_app"]
