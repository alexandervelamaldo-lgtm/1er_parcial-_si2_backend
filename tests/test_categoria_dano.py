"""Pruebas unitarias para CategoriaDano, normalización de texto libre
y coherencia de filtrado de categorías de taller."""

from app.models.enums import CategoriaDano, normalizar_categoria_dano
from app.services.gestion_operativa_web.taller_filtro_service import (
    categorias_permitidas_por_problema,
)
from app.services.gestion_operativa_web.demanda_matching_service import especialidad_compatible


# ---------------------------------------------------------------------------
# normalizar_categoria_dano
# ---------------------------------------------------------------------------

def test_normaliza_pinchazo_desde_texto_libre() -> None:
    assert normalizar_categoria_dano("llanta pinchada") == CategoriaDano.PINCHAZO
    assert normalizar_categoria_dano("ponchada") == CategoriaDano.PINCHAZO
    assert normalizar_categoria_dano("neumático") == CategoriaDano.PINCHAZO


def test_normaliza_choque_carroceria() -> None:
    assert normalizar_categoria_dano("choque frontal") == CategoriaDano.CHOQUE_CARROCERIA
    assert normalizar_categoria_dano("colisión") == CategoriaDano.CHOQUE_CARROCERIA
    assert normalizar_categoria_dano("chaperia_pintura") == CategoriaDano.CHOQUE_CARROCERIA


def test_normaliza_dano_electrico() -> None:
    assert normalizar_categoria_dano("batería descargada") == CategoriaDano.DANO_ELECTRICO
    assert normalizar_categoria_dano("falla eléctrica") == CategoriaDano.DANO_ELECTRICO
    assert normalizar_categoria_dano("electricidad") == CategoriaDano.DANO_ELECTRICO


def test_normaliza_falla_mecanica() -> None:
    assert normalizar_categoria_dano("falla mecánica") == CategoriaDano.FALLA_MECANICA
    assert normalizar_categoria_dano("motor") == CategoriaDano.FALLA_MECANICA
    assert normalizar_categoria_dano("sobrecalentamiento") == CategoriaDano.FALLA_MECANICA


def test_normaliza_suspension() -> None:
    assert normalizar_categoria_dano("suspensión") == CategoriaDano.SUSPENSION
    assert normalizar_categoria_dano("amortiguador") == CategoriaDano.SUSPENSION


def test_normaliza_valor_enum_directo() -> None:
    assert normalizar_categoria_dano("pinchazo") == CategoriaDano.PINCHAZO
    assert normalizar_categoria_dano("dano_electrico") == CategoriaDano.DANO_ELECTRICO
    assert normalizar_categoria_dano("general") == CategoriaDano.GENERAL


def test_normaliza_none_y_vacio_a_general() -> None:
    assert normalizar_categoria_dano(None) == CategoriaDano.GENERAL
    assert normalizar_categoria_dano("") == CategoriaDano.GENERAL
    assert normalizar_categoria_dano("   ") == CategoriaDano.GENERAL


def test_normaliza_texto_desconocido_a_general() -> None:
    assert normalizar_categoria_dano("problema extraño XYZ") == CategoriaDano.GENERAL


# ---------------------------------------------------------------------------
# categorias_permitidas_por_problema — filtrado ESTRICTO por tipo de servicio
# (cada daño específico devuelve sólo su categoría, sin comodín 'general')
# ---------------------------------------------------------------------------

def test_pinchazo_retorna_solo_llantas() -> None:
    result = categorias_permitidas_por_problema("pinchazo")
    assert result == {"llantas"}
    assert "general" not in result


def test_choque_retorna_solo_chaperia() -> None:
    result = categorias_permitidas_por_problema("choque_carroceria")
    assert result == {"chaperia_pintura"}
    assert "general" not in result


def test_dano_electrico_retorna_solo_electricidad() -> None:
    result = categorias_permitidas_por_problema("dano_electrico")
    assert result == {"electricidad"}
    assert "general" not in result


def test_falla_mecanica_retorna_solo_motor() -> None:
    result = categorias_permitidas_por_problema("falla_mecanica")
    assert result == {"motor"}
    assert "general" not in result


def test_suspension_retorna_solo_suspension() -> None:
    result = categorias_permitidas_por_problema("suspension")
    assert result == {"suspension"}
    assert "general" not in result


def test_texto_libre_bateria_descargada_retorna_electricidad() -> None:
    result = categorias_permitidas_por_problema("batería descargada")
    assert result == {"electricidad"}


# ---------------------------------------------------------------------------
# especialidad_compatible con CategoriaDano
# ---------------------------------------------------------------------------

def test_especialidad_compatible_pinchazo() -> None:
    assert especialidad_compatible("pinchazo", "Servicio de llantas y neumáticos")
    assert not especialidad_compatible("pinchazo", "Diagnóstico eléctrico avanzado")


def test_especialidad_compatible_dano_electrico() -> None:
    assert especialidad_compatible("dano_electrico", "Electricidad automotriz")
    assert not especialidad_compatible("dano_electrico", "Chapería y pintura")


def test_especialidad_compatible_falla_mecanica() -> None:
    assert especialidad_compatible("falla_mecanica", "Motor y mecánica general")
    assert not especialidad_compatible("falla_mecanica", "Polarizado de vidrios")


def test_especialidad_compatible_choque_carroceria() -> None:
    assert especialidad_compatible("choque_carroceria", "Chapería y pintura automotriz")
    assert not especialidad_compatible("choque_carroceria", "Solo servicio de llantas")


def test_especialidad_compatible_general_acepta_cualquier_cosa() -> None:
    assert especialidad_compatible("general", "Cualquier especialidad X")
    assert especialidad_compatible(None, "Electricidad avanzada")
