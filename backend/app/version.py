"""Single source of truth for the release version.

The frontend references its assets as ``app.js?v=<version>`` and carries the
same constant, and ``/health`` reports it. A test keeps all three identical, so
a release can never pair a new page with a browser's cached old script — the
failure that surfaced as "Cannot read properties of undefined (reading 'length')".
"""

APP_VERSION = "2.1.0"
