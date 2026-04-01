from fastapi import Request
from fastapi.responses import JSONResponse


async def unhandled_exception_handler(_: Request, exc: Exception) -> JSONResponse:
    return JSONResponse(
        status_code=500,
        content={
            "message": "Ocurrió un error interno no controlado",
            "detail": str(exc),
        },
    )
