from dataclasses import dataclass

# Fixed discount percentage applied when a vehicle's brand matches the
# taller's single associated brand (marca_asociada).
DESCUENTO_MARCA_ASOCIADA_PCT: float = 15.0


def marcas_coinciden(marca_asociada: str | None, marca_vehiculo: str | None) -> bool:
    """Return True when both strings are non-empty and equal (case-insensitive)."""
    if not marca_asociada or not marca_vehiculo:
        return False
    return marca_asociada.strip().upper() == marca_vehiculo.strip().upper()


def descuento_por_marca_asociada(
    marca_asociada: str | None,
    marca_vehiculo: str | None,
) -> float | None:
    """Return DESCUENTO_MARCA_ASOCIADA_PCT when brands match, else None."""
    return DESCUENTO_MARCA_ASOCIADA_PCT if marcas_coinciden(marca_asociada, marca_vehiculo) else None


@dataclass(slots=True)
class PresupuestoEstimado:
    presupuesto_min: float | None
    presupuesto_max: float | None
    presupuesto_descuento_min: float | None
    presupuesto_descuento_max: float | None
    descuento_porcentaje_aplicado: float | None
    tiempo_reparacion_horas: float | None


_DEFAULTS: dict[str, tuple[float, float]] = {
    "chaperia_pintura": (900.0, 3200.0),
    "llantas": (120.0, 650.0),
    "motor": (600.0, 4500.0),
    "electricidad": (250.0, 1800.0),
    "suspension": (350.0, 2400.0),
    "general": (200.0, 1800.0),
}

_TIEMPOS_HORAS: dict[str, float] = {
    "chaperia_pintura": 24.0,
    "llantas": 2.0,
    "motor": 18.0,
    "electricidad": 6.0,
    "suspension": 8.0,
    "general": 6.0,
}


def _normalize(key: str | None) -> str:
    if not key:
        return "general"
    k = key.strip().lower()
    aliases = {
        "chaperia": "chaperia_pintura",
        "pintura": "chaperia_pintura",
        "chapisteria": "chaperia_pintura",
        "llanta": "llantas",
        "neumatico": "llantas",
        "rueda": "llantas",
        "motor": "motor",
        "motores": "motor",
        "electrico": "electricidad",
        "electricos": "electricidad",
        "electricidad": "electricidad",
        "bateria": "electricidad",
        "suspension": "suspension",
        "amortiguacion": "suspension",
        "general": "general",
    }
    return aliases.get(k, k)


def _normalize_brand(marca: str | None) -> str | None:
    if not marca:
        return None
    value = str(marca).strip().upper()
    return value or None


def _get_discount_percent(descuentos_marca: dict | None, marca: str | None) -> float | None:
    brand = _normalize_brand(marca)
    if not brand:
        return None
    descuentos = descuentos_marca or {}
    raw = descuentos.get(brand)
    if raw is None:
        return None
    try:
        pct = float(raw)
    except Exception:
        return None
    if pct <= 0:
        return None
    return min(100.0, max(0.0, pct))


def _apply_discount(amount: float | None, discount_percent: float | None) -> float | None:
    if amount is None or discount_percent is None:
        return None
    factor = 1.0 - (discount_percent / 100.0)
    return round(amount * factor, 2)


def calcular_presupuesto_estimado(
    dano_categoria: str | None,
    tarifas_base: dict | None,
    *,
    descuentos_marca: dict | None = None,
    marca_vehiculo: str | None = None,
) -> PresupuestoEstimado:
    key = _normalize(dano_categoria)
    tarifas = tarifas_base or {}
    discount_percent = _get_discount_percent(descuentos_marca, marca_vehiculo)
    base_value = tarifas.get(key)
    if isinstance(base_value, (int, float)):
        base = float(base_value)
        min_v = round(base * 0.85, 2)
        max_v = round(base * 1.25, 2)
        return PresupuestoEstimado(
            presupuesto_min=min_v,
            presupuesto_max=max_v,
            presupuesto_descuento_min=_apply_discount(min_v, discount_percent),
            presupuesto_descuento_max=_apply_discount(max_v, discount_percent),
            descuento_porcentaje_aplicado=discount_percent,
            tiempo_reparacion_horas=_TIEMPOS_HORAS.get(key, _TIEMPOS_HORAS["general"]),
        )

    default_min, default_max = _DEFAULTS.get(key, _DEFAULTS["general"])
    return PresupuestoEstimado(
        presupuesto_min=default_min,
        presupuesto_descuento_min=_apply_discount(default_min, discount_percent),
        presupuesto_descuento_max=_apply_discount(default_max, discount_percent),
        descuento_porcentaje_aplicado=discount_percent,
        presupuesto_max=default_max,
        tiempo_reparacion_horas=_TIEMPOS_HORAS.get(key, _TIEMPOS_HORAS["general"]),
    )
