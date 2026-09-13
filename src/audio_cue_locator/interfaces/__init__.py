"""Interfaces: external transport boundaries (REST API today) that expose
Application to a caller without redefining Core or Application contracts
(docs/architecture.md, "REST API"; M5-01).

This layer may depend on Application. It must not be depended on by Core or
Application, and it must not reach past Application into a concrete
Infrastructure adapter (matcher, SQLite, filesystem, or executor).
"""
