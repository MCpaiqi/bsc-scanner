"""Single-owner analyst authorization; public research remains readable."""

import re
from secrets import compare_digest
from typing import Annotated

from fastapi import Depends, HTTPException, Request, Response
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from smms.config.settings import Settings, get_settings

_bearer = HTTPBearer(auto_error=False, scheme_name="AnalystBearer")
_Settings = Annotated[Settings, Depends(get_settings)]
_Credentials = Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer)]


def _authorized(request: Request, settings: Settings, credentials: HTTPAuthorizationCredentials | None) -> bool:
    if settings.analyst_key is None or credentials is None or len(request.headers.getlist("authorization")) != 1:
        return False
    supplied = credentials.credentials
    if not re.fullmatch(r"[A-Za-z0-9_-]{32,256}", supplied):
        return False
    return compare_digest(supplied.encode("ascii"), settings.analyst_key.get_secret_value().encode("ascii"))


def analyst_write_access(request: Request, settings: _Settings, credentials: _Credentials) -> None:
    if request.method in {"GET", "HEAD", "OPTIONS"}:
        return
    if settings.analyst_key is None:
        raise HTTPException(503, {"code": "ANALYST_READ_ONLY", "message": "Analyst writes are disabled"})
    if not _authorized(request, settings, credentials):
        raise HTTPException(401, {"code": "ANALYST_UNAUTHORIZED", "message": "Analyst authorization required"},
                            headers={"WWW-Authenticate": "Bearer"})


def access_status(request: Request, response: Response, settings: _Settings, credentials: _Credentials):
    response.headers["Cache-Control"] = "no-store"
    return {"enabled": settings.analyst_key is not None,
            "authorized": _authorized(request, settings, credentials)}
