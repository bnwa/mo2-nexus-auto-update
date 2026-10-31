"""Application layer: orchestrates scans, downloads and installs.

Depends on the domain and tracking layers and defines the ports in ``ports``
that adapters implement. Never imports adapters (``host``, ``nexus``), mobase or
Qt, so a change in the beta Nexus v2 API cannot reach it.
"""
