"""Unit tests for the taller brand-association discount logic.

Tests cover:
- marcas_coinciden(): case-insensitive string comparison
- descuento_por_marca_asociada(): returns 15.0 on match, None otherwise
- Edge cases: None, empty string, whitespace
- Total calculation with discount applied
"""

import pytest

from app.services.gestion_operativa_web.taller_presupuesto_service import (
    DESCUENTO_MARCA_ASOCIADA_PCT,
    descuento_por_marca_asociada,
    marcas_coinciden,
)


# ── marcas_coinciden ──────────────────────────────────────────────────────────


class TestMarcasCoinciden:
    def test_exact_match_uppercase(self):
        assert marcas_coinciden("TOYOTA", "TOYOTA") is True

    def test_case_insensitive_lower_vs_upper(self):
        assert marcas_coinciden("toyota", "TOYOTA") is True

    def test_case_insensitive_upper_vs_lower(self):
        assert marcas_coinciden("TOYOTA", "toyota") is True

    def test_case_insensitive_mixed(self):
        assert marcas_coinciden("Toyota", "TOYOTA") is True

    def test_case_insensitive_both_mixed(self):
        assert marcas_coinciden("Toyota", "toyota") is True

    def test_different_brands(self):
        assert marcas_coinciden("TOYOTA", "HONDA") is False

    def test_different_brands_mixed_case(self):
        assert marcas_coinciden("Toyota", "Honda") is False

    def test_none_marca_asociada(self):
        assert marcas_coinciden(None, "TOYOTA") is False

    def test_none_marca_vehiculo(self):
        assert marcas_coinciden("TOYOTA", None) is False

    def test_both_none(self):
        assert marcas_coinciden(None, None) is False

    def test_empty_string_marca_asociada(self):
        assert marcas_coinciden("", "TOYOTA") is False

    def test_empty_string_marca_vehiculo(self):
        assert marcas_coinciden("TOYOTA", "") is False

    def test_both_empty(self):
        assert marcas_coinciden("", "") is False

    def test_whitespace_only_marca_asociada(self):
        assert marcas_coinciden("   ", "TOYOTA") is False

    def test_whitespace_stripped_match(self):
        # Leading/trailing spaces are stripped before comparison
        assert marcas_coinciden("  Toyota  ", "TOYOTA") is True

    def test_whitespace_stripped_vehicle(self):
        assert marcas_coinciden("TOYOTA", "  toyota  ") is True

    def test_partial_match_is_false(self):
        assert marcas_coinciden("TOYO", "TOYOTA") is False

    def test_subset_match_is_false(self):
        assert marcas_coinciden("TOYOTA", "TOYOTA COROLLA") is False


# ── descuento_por_marca_asociada ──────────────────────────────────────────────


class TestDescuentoPorMarcaAsociada:
    def test_returns_15_on_match(self):
        result = descuento_por_marca_asociada("TOYOTA", "TOYOTA")
        assert result == DESCUENTO_MARCA_ASOCIADA_PCT
        assert result == 15.0

    def test_returns_15_on_case_insensitive_match(self):
        assert descuento_por_marca_asociada("Toyota", "TOYOTA") == 15.0
        assert descuento_por_marca_asociada("toyota", "Toyota") == 15.0

    def test_returns_none_on_mismatch(self):
        assert descuento_por_marca_asociada("TOYOTA", "HONDA") is None

    def test_returns_none_when_taller_has_no_marca(self):
        assert descuento_por_marca_asociada(None, "TOYOTA") is None

    def test_returns_none_when_vehicle_has_no_marca(self):
        assert descuento_por_marca_asociada("TOYOTA", None) is None

    def test_returns_none_when_both_none(self):
        assert descuento_por_marca_asociada(None, None) is None

    def test_returns_none_when_marca_asociada_empty(self):
        assert descuento_por_marca_asociada("", "TOYOTA") is None

    def test_returns_none_when_marca_vehiculo_empty(self):
        assert descuento_por_marca_asociada("TOYOTA", "") is None

    def test_whitespace_stripped_returns_15(self):
        assert descuento_por_marca_asociada("  Toyota  ", "  toyota  ") == 15.0

    def test_other_brands_match(self):
        assert descuento_por_marca_asociada("FORD", "Ford") == 15.0
        assert descuento_por_marca_asociada("BMW", "BMW") == 15.0
        assert descuento_por_marca_asociada("CHEVROLET", "chevrolet") == 15.0


# ── Total calculation with discount applied ───────────────────────────────────


class TestTotalConDescuento:
    """Verify the arithmetic used in the router (not the router itself)."""

    def _apply(self, total: float, pct: float | None) -> float:
        if pct is not None:
            return round(total * (1.0 - pct / 100.0), 2)
        return total

    def test_no_discount_preserves_total(self):
        assert self._apply(1000.0, None) == 1000.0

    def test_15_percent_discount_on_round_amount(self):
        assert self._apply(1000.0, 15.0) == 850.0

    def test_15_percent_discount_on_decimal_amount(self):
        # 1200.50 × 0.85 = 1020.425 → Python banker's rounding → 1020.42
        assert self._apply(1200.50, 15.0) == 1020.42

    def test_discount_applied_correctly_for_small_total(self):
        assert self._apply(100.0, 15.0) == 85.0

    def test_discount_constant_is_15(self):
        assert DESCUENTO_MARCA_ASOCIADA_PCT == 15.0
