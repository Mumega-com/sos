"""SOS Dispatcher — Python reference implementation.

This is the bare-metal-friendly dispatcher that runs on any Linux host (VPS,
Raspberry Pi, developer laptop). Speaks the dispatcher protocol defined in
docs/plans/2026-04-17-dispatcher-protocol.md.

Same contract as the CF Worker dispatcher at workers/sos-dispatcher/. Both
must pass tests/contracts/test_dispatcher_protocol.py for the portability
guarantee to hold.
"""
