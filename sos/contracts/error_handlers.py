"""FastAPI exception handler for SOS typed errors (v0.4.2+).

Usage in any FastAPI app module:

    from sos.contracts.error_handlers import register_sos_error_handler
    app = FastAPI(...)
    register_sos_error_handler(app)

After registration, any ``SOSError`` subclass raised in a route handler
will be serialised to a JSON response with the correct HTTP status code:

    {"error": {"code": "SOS-4001", "message": "...", "details": {...}}}
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from fastapi import FastAPI
from fastapi.responses import JSONResponse
from starlette.requests import Request

from sos.contracts.errors import SOSError

if TYPE_CHECKING:
    pass


def register_sos_error_handler(app: FastAPI) -> None:
    """Register a global SOSError exception handler on *app*.

    Call once during app initialisation — before any requests are served.
    Do NOT call multiple times; FastAPI does not deduplicate handlers.
    """

    @app.exception_handler(SOSError)
    async def _handle_sos_error(request: Request, exc: SOSError) -> JSONResponse:
        return JSONResponse(
            status_code=exc.http_status,
            content={"error": exc.to_dict()},
        )
