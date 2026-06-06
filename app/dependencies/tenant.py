from fastapi import HTTPException, status
from jose import JWTError
from starlette.requests import HTTPConnection

from app.config import get_settings
from app.utils.auth import decode_token


# Routes that should never have a tenant enforced. They are either pre-login
# (so we cannot trust any tenant claim yet) or public-by-design.
_PUBLIC_PATH_PREFIXES: tuple[str, ...] = (
    "/docs",
    "/redoc",
    "/openapi.json",
    "/health",
    "/tenants/public",   # used by the login page to list available tenants
)


def _is_public_path(path: str) -> bool:
    return any(path == p or path.startswith(p + "/") for p in _PUBLIC_PATH_PREFIXES)


def resolve_tenant_key(conn: HTTPConnection) -> str:
    """
    Pick the tenant key for the current request and verify it is one we know
    about. The lookup order is intentionally:

      1. ``X-Tenant`` (or legacy ``X-Tenant-Id``) header — explicit and cheap.
      2. ``?tenant=`` query string — used by the WebSocket handshake where
         we cannot add custom headers easily.
      3. JWT ``tenant`` claim — last resort, only when the request omitted
         both header and query.
      4. ``DEFAULT_TENANT`` setting — fallback for unauthenticated public
         endpoints in development.

    If the caller supplies an *explicit* tenant key (header or query) that
    we have never heard of, we raise 404 instead of silently falling back to
    the default. The previous silent-fallback behaviour was a real isolation
    leak: a malicious client could send ``X-Tenant: hacker`` and then read
    the default tenant's data instead of getting an error.
    """
    settings = get_settings()

    header_value = conn.headers.get("x-tenant") or conn.headers.get("x-tenant-id")
    header_tenant = (header_value or "").strip()
    query_tenant = (conn.query_params.get("tenant") or "").strip()

    # El marcador "*" es del super-admin (cross-tenant). NO es un tenant
    # real — lo tratamos como ausente para que la resolución caiga al
    # default y NO devuelva 404. La auth del super-admin se valida
    # aparte en get_current_user (mira la control DB).
    if header_tenant == "*":
        header_tenant = ""
    if query_tenant == "*":
        query_tenant = ""

    # An explicit, unknown tenant is a hard error — never fall through.
    explicit_tenant = header_tenant or query_tenant
    if explicit_tenant and explicit_tenant not in settings.tenant_databases:
        if not _is_public_path(conn.url.path):
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Tenant '{explicit_tenant}' no existe",
            )
        # Public routes can be hit with any tenant key — fall back to default.
        return settings.default_tenant or "default"

    if explicit_tenant:
        return explicit_tenant

    # No explicit tenant — try the JWT claim.
    authorization = conn.headers.get("authorization") or ""
    if authorization.lower().startswith("bearer "):
        token = authorization.split(" ", 1)[1].strip()
        try:
            payload = decode_token(token)
            tenant_value = payload.get("tenant")
            if isinstance(tenant_value, str):
                claim = tenant_value.strip()
                if claim and claim in settings.tenant_databases:
                    return claim
        except JWTError:
            pass

    return settings.default_tenant or "default"

