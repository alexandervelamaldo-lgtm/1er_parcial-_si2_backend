from app.services.gestion_operativa_web.taller_presupuesto_service import calcular_presupuesto_estimado


def test_presupuesto_defaults_general() -> None:
    p = calcular_presupuesto_estimado(dano_categoria=None, tarifas_base=None)
    assert p.presupuesto_min is not None
    assert p.presupuesto_max is not None
    assert p.presupuesto_min < p.presupuesto_max
    assert p.descuento_porcentaje_aplicado is None
    assert p.tiempo_reparacion_horas is not None


def test_presupuesto_alias_llanta_usa_defaults_llantas() -> None:
    p = calcular_presupuesto_estimado(dano_categoria="llanta", tarifas_base=None)
    assert p.presupuesto_min == 120.0
    assert p.presupuesto_max == 650.0
    assert p.tiempo_reparacion_horas == 2.0


def test_presupuesto_con_tarifa_base_aplica_rango() -> None:
    p = calcular_presupuesto_estimado(dano_categoria="motor", tarifas_base={"motor": 2000})
    assert p.presupuesto_min == 1700.0
    assert p.presupuesto_max == 2500.0
    assert p.tiempo_reparacion_horas == 18.0


def test_presupuesto_con_descuento_por_marca() -> None:
    p = calcular_presupuesto_estimado(
        dano_categoria="motor",
        tarifas_base={"motor": 2000},
        descuentos_marca={"TOYOTA": 15},
        marca_vehiculo="Toyota",
    )
    assert p.descuento_porcentaje_aplicado == 15.0
    assert p.presupuesto_descuento_min == 1445.0
    assert p.presupuesto_descuento_max == 2125.0
