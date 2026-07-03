from app.services.gestion_operativa_web.taller_filtro_service import categorias_permitidas_por_problema


# Filtrado ESTRICTO: cada daño específico devuelve SÓLO su categoría
# especializada, sin el comodín 'general'. El fallback a talleres generales
# cuando no hay especializados se resuelve en el endpoint /talleres/mapa.

def test_filtrado_llanta_pinchada() -> None:
    assert categorias_permitidas_por_problema("llanta pinchada") == {"llantas"}


def test_filtrado_falla_electrica() -> None:
    assert categorias_permitidas_por_problema("falla eléctrica") == {"electricidad"}


def test_filtrado_motor() -> None:
    assert categorias_permitidas_por_problema("motor") == {"motor"}


def test_filtrado_suspension() -> None:
    assert categorias_permitidas_por_problema("suspensión") == {"suspension"}


def test_filtrado_chaperia_pintura() -> None:
    assert categorias_permitidas_por_problema("chapería y pintura") == {"chaperia_pintura"}


def test_filtrado_choque() -> None:
    assert categorias_permitidas_por_problema("choque frontal") == {"chaperia_pintura"}


def test_filtrado_bateria_descargada() -> None:
    assert categorias_permitidas_por_problema("batería descargada") == {"electricidad"}


def test_filtrado_no_incluye_general_en_dano_especifico() -> None:
    # Estricto: 'general' nunca aparece como comodín en un daño especializado.
    for dano in ("pinchazo", "choque_carroceria", "dano_electrico", "falla_mecanica", "suspension"):
        assert "general" not in categorias_permitidas_por_problema(dano)


def test_filtrado_general_y_desconocido_caen_en_general() -> None:
    assert categorias_permitidas_por_problema("general") == {"general"}
    assert categorias_permitidas_por_problema("algo no mapeado xyz") == {"general"}
    assert categorias_permitidas_por_problema(None) == {"general"}
