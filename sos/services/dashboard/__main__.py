from __future__ import annotations

import uvicorn

uvicorn.run("sos.services.dashboard.app:app", host="0.0.0.0", port=8090)
