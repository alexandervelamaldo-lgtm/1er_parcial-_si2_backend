import asyncio
import os

import asyncpg


async def _test(dsn: str) -> None:
    print(f"DSN: {dsn}")
    for ssl in (None, False, True):
        label = "default" if ssl is None else f"ssl={ssl}"
        try:
            conn = await asyncpg.connect(dsn, ssl=ssl)
        except Exception as exc:
            print(f"CONNECT ERROR ({label}): {type(exc).__name__}: {exc}")
            continue
        try:
            value = await conn.fetchval("select 1")
            print(f"QUERY OK ({label}): {value}")
        except Exception as exc:
            print(f"QUERY ERROR ({label}): {type(exc).__name__}: {exc}")
        finally:
            await conn.close()
        return


async def main() -> None:
    dsn = os.environ.get("DB_SMOKE_DSN") or "postgresql://postgres:postgres@127.0.0.1:5432/emergency_db"
    await _test(dsn)
    await _test("postgresql://postgres:postgres@127.0.0.1:5432/postgres")


if __name__ == "__main__":
    asyncio.run(main())
