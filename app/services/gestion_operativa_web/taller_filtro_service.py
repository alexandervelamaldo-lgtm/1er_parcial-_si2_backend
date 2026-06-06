from app.models.enums import CategoriaDano, normalizar_categoria_dano

# Mapeo ESTRICTO de CategoriaDano → categoría de taller que la atiende.
#
# Antes cada daño específico incluía además "general" como comodín, de modo que
# un pinchazo mostraba también talleres multiservicio. El requerimiento de
# negocio ahora es filtrado estricto por tipo de servicio: una gomería sólo
# debe ver talleres de la categoría 'llantas', un choque sólo 'chaperia_pintura',
# etc. Por eso cada daño específico mapea a UNA sola categoría especializada.
#
# Nota: 'general' sigue siendo la categoría de la solicitud GENERAL/desconocida.
# El endpoint /talleres/mapa aplica un fallback a 'general' SÓLO cuando no hay
# ningún taller especializado disponible, para no dejar al cliente sin opciones.
_CATEGORIAS_TALLER_POR_DANO: dict[CategoriaDano, set[str]] = {
    CategoriaDano.PINCHAZO: {"llantas"},
    CategoriaDano.CHOQUE_CARROCERIA: {"chaperia_pintura"},
    CategoriaDano.DANO_ELECTRICO: {"electricidad"},
    CategoriaDano.FALLA_MECANICA: {"motor"},
    CategoriaDano.SUSPENSION: {"suspension"},
    CategoriaDano.CHAPERIA_PINTURA: {"chaperia_pintura"},
    CategoriaDano.GENERAL: {"general"},
}


def categorias_permitidas_por_problema(dano_categoria: str | None) -> set[str]:
    """Devuelve la categoría de taller que atiende el tipo de daño (filtrado estricto).

    Acepta tanto texto libre (legado) como valores del enum CategoriaDano. Cada
    daño específico devuelve SÓLO su categoría especializada (sin el comodín
    'general'); un daño desconocido cae en 'general'. El fallback a talleres
    generales cuando no hay especializados se resuelve en la capa de endpoint.
    """
    cat = normalizar_categoria_dano(dano_categoria)
    return _CATEGORIAS_TALLER_POR_DANO.get(cat, {"general"})
