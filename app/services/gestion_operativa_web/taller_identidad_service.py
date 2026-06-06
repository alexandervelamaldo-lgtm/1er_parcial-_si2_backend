import re
import unicodedata


def normalize_workshop_name(value: str | None) -> str:
    normalized = unicodedata.normalize("NFKD", (value or "").strip().lower())
    plain = "".join(ch for ch in normalized if not unicodedata.combining(ch))
    return re.sub(r"\s+", " ", plain).strip()


def normalize_phone(value: str | None) -> str:
    return re.sub(r"\D+", "", value or "")


def same_workshop_identity(
    *,
    left_name: str | None,
    left_phone: str | None,
    right_name: str | None,
    right_phone: str | None,
) -> bool:
    return normalize_workshop_name(left_name) == normalize_workshop_name(right_name) and normalize_phone(left_phone) == normalize_phone(right_phone)
