from app.services.gestion_operativa_web.taller_identidad_service import (
    normalize_phone,
    normalize_workshop_name,
    same_workshop_identity,
)


def test_normalize_workshop_name_removes_accents_and_extra_spaces() -> None:
    assert normalize_workshop_name("  Taller Élite   Norte ") == "taller elite norte"


def test_normalize_phone_keeps_only_digits() -> None:
    assert normalize_phone("+591 700-000-01") == "59170000001"


def test_same_workshop_identity_detects_duplicate_by_name_and_phone() -> None:
    assert same_workshop_identity(
        left_name="Taller Elite Norte",
        left_phone="70000001",
        right_name="táller   élite norte",
        right_phone="7000-0001",
    )


def test_same_workshop_identity_rejects_distinct_workshop() -> None:
    assert not same_workshop_identity(
        left_name="Taller Elite Norte",
        left_phone="70000001",
        right_name="Taller Sur Motor",
        right_phone="70000001",
    )
