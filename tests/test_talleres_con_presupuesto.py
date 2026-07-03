"""Pruebas unitarias del helper ``_build_taller_con_presupuesto``.

El endpoint completo necesita DB + Mapbox + HTTP, así que aquí cubrimos
sólo la lógica de armado del payload por taller — el caso interesante:
cómo combina presupuesto base + descuento por marca asociada + score.

Para mantener los tests aislados de DB usamos objetos mock que simulan
los modelos SQLAlchemy con sólo los atributos que el helper lee.
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest


# ── Fixtures helpers ────────────────────────────────────────────────────


def _make_solicitud(
    *,
    categoria_dano: str = "general",
    vehiculo_marca: str | None = None,
    moneda_costo: str = "BOB",
    descripcion: str = "Falla mecánica menor",
    tipo_incidente_nombre: str = "Falla mecánica",
):
    """Construye un mock de Solicitud con la forma que el helper espera."""
    vehiculo = SimpleNamespace(marca=vehiculo_marca) if vehiculo_marca else None
    tipo_incidente = SimpleNamespace(nombre=tipo_incidente_nombre)
    return SimpleNamespace(
        id=1,
        latitud_incidente=-17.78,
        longitud_incidente=-63.18,
        descripcion=descripcion,
        condicion_vehiculo="operativo",
        categoria_dano=categoria_dano,
        moneda_costo=moneda_costo,
        vehiculo=vehiculo,
        tipo_incidente=tipo_incidente,
        etiquetas_ia="",
    )


def _make_taller(
    *,
    nombre: str = "Taller Centro",
    lat: float = -17.79,
    lng: float = -63.19,
    servicios: str = "motor|frenos",
    tarifas_base: dict | None = None,
    descuentos_marca: dict | None = None,
    marca_asociada: str | None = None,
    rating: float = 4.2,
    capacidad: int = 3,
):
    return SimpleNamespace(
        id=10,
        nombre=nombre,
        direccion="Av. Test 123",
        latitud=lat,
        longitud=lng,
        servicios=servicios,
        tarifas_base=tarifas_base or {},
        descuentos_marca=descuentos_marca or {},
        marca_asociada=marca_asociada,
        rating_promedio=rating,
        rating_total=10,
        capacidad=capacidad,
        disponible=True,
    )


# ── Tests ───────────────────────────────────────────────────────────────


class TestBuildTallerConPresupuesto:
    def test_estructura_basica_del_payload(self):
        """El helper devuelve los campos contractados por el schema."""
        from app.routers.gestion_solicitudes.solicitudes import _build_taller_con_presupuesto

        solicitud = _make_solicitud(categoria_dano="motor")
        taller = _make_taller(tarifas_base={"motor": 1000.0})

        out = _build_taller_con_presupuesto(solicitud, taller, distancia_km=3.5, eta_min=8)

        assert out.taller_id == taller.id
        assert out.nombre == taller.nombre
        assert out.lat == taller.latitud and out.lng == taller.longitud
        assert out.distancia_km == 3.5
        assert out.eta_min == 8
        assert 0.0 <= out.score <= 1.0
        assert out.presupuesto.moneda == "BOB"
        # Con tarifa 1000 → rango 850..1250 → monto_base ≈ promedio
        assert out.presupuesto.monto_base > 0
        assert out.presupuesto.rango_min <= out.presupuesto.monto_final <= out.presupuesto.rango_max

    def test_descuento_15pct_cuando_marca_vehiculo_coincide_con_marca_asociada(self):
        """El descuento global por marca asociada (15%) debe ganarle a
        otros descuentos individuales y reducir el monto final."""
        from app.routers.gestion_solicitudes.solicitudes import _build_taller_con_presupuesto

        solicitud = _make_solicitud(vehiculo_marca="Toyota", categoria_dano="motor")
        taller = _make_taller(
            tarifas_base={"motor": 1000.0},
            marca_asociada="TOYOTA",
        )

        out = _build_taller_con_presupuesto(solicitud, taller, distancia_km=2.0, eta_min=5)

        assert out.marca_asociada_descuento is True
        assert out.presupuesto.descuento_pct == 15.0
        # Promedio del rango 850..1250 = 1050 → con 15% dto = 892.5
        assert out.presupuesto.monto_final < out.presupuesto.monto_base
        assert out.presupuesto.motivo_descuento == "Marca asociada del taller (TOYOTA)"

    def test_sin_marca_asociada_no_aplica_descuento_15pct(self):
        from app.routers.gestion_solicitudes.solicitudes import _build_taller_con_presupuesto

        solicitud = _make_solicitud(vehiculo_marca="Hyundai", categoria_dano="motor")
        taller = _make_taller(
            tarifas_base={"motor": 1000.0},
            marca_asociada="TOYOTA",   # ≠ Hyundai → no aplica
        )

        out = _build_taller_con_presupuesto(solicitud, taller, distancia_km=2.0, eta_min=5)

        assert out.marca_asociada_descuento is False
        # Puede haber otro descuento por descuentos_marca (acá no hay), o ninguno.
        assert out.presupuesto.descuento_pct in (None, 0.0)

    def test_match_especializacion_cuando_categoria_coincide(self):
        """_keyword_matches_for_workshop espera categorías como
        'mecanica' / 'electrico' / 'llantas' en taller.servicios — no
        términos crudos como 'motor'. El match cruza descripción del
        incidente con alias de cada categoría."""
        from app.routers.gestion_solicitudes.solicitudes import _build_taller_con_presupuesto

        # Descripción menciona "motor" → matchea categoría "mecanica"
        solicitud = _make_solicitud(categoria_dano="motor", descripcion="falla del motor")
        taller_match    = _make_taller(servicios="mecanica|grua")
        taller_no_match = _make_taller(servicios="llantas|combustible")

        out_match    = _build_taller_con_presupuesto(solicitud, taller_match,    distancia_km=4.0, eta_min=9)
        out_no_match = _build_taller_con_presupuesto(solicitud, taller_no_match, distancia_km=4.0, eta_min=9)

        assert out_match.match_especializacion is True
        assert out_no_match.match_especializacion is False
        # El match debe subir el score
        assert out_match.score > out_no_match.score

    def test_score_premia_cercania(self):
        from app.routers.gestion_solicitudes.solicitudes import _build_taller_con_presupuesto

        solicitud = _make_solicitud(categoria_dano="motor")
        taller = _make_taller(tarifas_base={"motor": 1000.0})

        cerca = _build_taller_con_presupuesto(solicitud, taller, distancia_km=2.0, eta_min=5)
        lejos = _build_taller_con_presupuesto(solicitud, taller, distancia_km=20.0, eta_min=35)

        assert cerca.score > lejos.score

    def test_score_premia_rating_alto(self):
        from app.routers.gestion_solicitudes.solicitudes import _build_taller_con_presupuesto

        solicitud = _make_solicitud(categoria_dano="motor")
        t_buen_rating = _make_taller(rating=5.0)
        t_mal_rating  = _make_taller(rating=2.0)

        cerca_buen = _build_taller_con_presupuesto(solicitud, t_buen_rating, distancia_km=4.0, eta_min=9)
        cerca_malo = _build_taller_con_presupuesto(solicitud, t_mal_rating,  distancia_km=4.0, eta_min=9)

        assert cerca_buen.score > cerca_malo.score

    def test_eta_none_cuando_mapbox_falla(self):
        from app.routers.gestion_solicitudes.solicitudes import _build_taller_con_presupuesto

        solicitud = _make_solicitud(categoria_dano="motor")
        taller = _make_taller()

        out = _build_taller_con_presupuesto(solicitud, taller, distancia_km=4.0, eta_min=None)

        assert out.eta_min is None

    def test_motivo_incluye_etiquetas_relevantes(self):
        """El campo `motivo` es el subtítulo que ve el cliente — debe explicar
        por qué este taller aparece arriba en la lista."""
        from app.routers.gestion_solicitudes.solicitudes import _build_taller_con_presupuesto

        solicitud = _make_solicitud(
            categoria_dano="motor",
            vehiculo_marca="Toyota",
            descripcion="falla del motor",   # gatilla match con categoría "mecanica"
        )
        taller = _make_taller(
            tarifas_base={"motor": 1000.0},
            marca_asociada="TOYOTA",
            servicios="mecanica|grua",
            rating=4.8,
        )
        out = _build_taller_con_presupuesto(solicitud, taller, distancia_km=2.0, eta_min=5)

        m = out.motivo.lower()
        assert "cerca" in m
        assert "especializado" in m
        assert "dto" in m or "%" in m
        assert "rating" in m or "excelente" in m

    def test_moneda_respeta_la_solicitud(self):
        """Si la solicitud está en USD, el presupuesto también."""
        from app.routers.gestion_solicitudes.solicitudes import _build_taller_con_presupuesto

        solicitud = _make_solicitud(moneda_costo="USD", categoria_dano="motor")
        taller = _make_taller(tarifas_base={"motor": 100.0})

        out = _build_taller_con_presupuesto(solicitud, taller, distancia_km=3.0, eta_min=7)

        assert out.presupuesto.moneda == "USD"


class TestCrossCheckIaVsTarifa:
    """Tests del cross-check entre estimación IA y tarifa del taller."""

    def test_sin_costo_ia_no_se_marca_revision(self):
        """Si la solicitud no tiene costo_estimado (IA no corrió o falló),
        diverge_ia_pct=None y requiere_revision=False."""
        from app.routers.gestion_solicitudes.solicitudes import _build_taller_con_presupuesto
        solicitud = _make_solicitud(categoria_dano="motor")
        # Aseguramos que NO tiene campos IA (el helper original no los pone)
        taller = _make_taller(tarifas_base={"motor": 1000.0})
        out = _build_taller_con_presupuesto(solicitud, taller, distancia_km=3.0, eta_min=8)
        assert out.presupuesto.diverge_ia_pct is None
        assert out.presupuesto.requiere_revision is False

    def test_marca_revision_si_divergencia_supera_80_pct(self):
        """IA estima 200 BOB, taller cobra 1000 BOB → diff=400% → revision."""
        from app.routers.gestion_solicitudes.solicitudes import _build_taller_con_presupuesto
        solicitud = _make_solicitud(categoria_dano="motor")
        # Inyectamos los campos IA en el mock
        solicitud.costo_estimado = 200.0
        solicitud.costo_estimado_min = 150.0
        solicitud.costo_estimado_max = 250.0
        taller = _make_taller(tarifas_base={"motor": 1000.0})
        out = _build_taller_con_presupuesto(solicitud, taller, distancia_km=3.0, eta_min=8)
        # monto_final será ~1000 (sin descuento), diff (1000-200)/200 = 400%
        assert out.presupuesto.diverge_ia_pct is not None
        assert out.presupuesto.diverge_ia_pct > 80.0
        assert out.presupuesto.requiere_revision is True
        assert "inusual" in out.motivo.lower() or "⚠" in out.motivo

    def test_no_marca_revision_si_divergencia_es_menor_80_pct(self):
        """IA estima 800 BOB, taller cobra 1000 → diff=25% → SIN revision."""
        from app.routers.gestion_solicitudes.solicitudes import _build_taller_con_presupuesto
        solicitud = _make_solicitud(categoria_dano="motor")
        solicitud.costo_estimado = 800.0
        solicitud.costo_estimado_min = 600.0
        solicitud.costo_estimado_max = 1100.0
        taller = _make_taller(tarifas_base={"motor": 1000.0})
        out = _build_taller_con_presupuesto(solicitud, taller, distancia_km=3.0, eta_min=8)
        assert out.presupuesto.diverge_ia_pct is not None
        assert out.presupuesto.diverge_ia_pct < 80.0
        assert out.presupuesto.requiere_revision is False
