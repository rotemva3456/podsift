from __future__ import annotations

from fastapi import APIRouter, Request

router = APIRouter()


@router.get("/companion/health")
def health(request: Request):
    return {"status": "ok", "library_connected": request.app.state.transcript_library.connected}
