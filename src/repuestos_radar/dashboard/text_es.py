"""Every user-visible string of the dashboard, in Rioplatense Spanish.

One module so (a) Mo reviews Spanish in one place, (b) an English mode later
is a translation of this file, not a hunt through page code.
"""

APP_TITLE = "Repuestos Radar"

# Login
PASSWORD_LABEL = "Contraseña"
LOGIN_BUTTON = "Entrar"
WRONG_PASSWORD = "Contraseña incorrecta."
NO_PASSWORD_CONFIGURED = (
    "Falta configurar la contraseña de la app (APP_PASSWORD). Avisale al equipo."
)

# Navigation
NAV_PRICES = "Precios"
NAV_DETAIL = "Detalle"
NAV_SETTINGS = "Ajustes"

# Freshness footer
UPDATED_PREFIX = "Actualizado:"
NO_DATA_AT_ALL = "Todavía no hay datos guardados."

# Home
BEST_PREFIX = "Mejor:"
MARGIN_GAIN = "Ganás {amount}"
MARGIN_LOSS = "Perdés {amount}"
NEEDS_REVIEW_DOT = "⚠ hay precios para revisar"
NO_DATA_TODAY = "sin datos de hoy"
SEE_DETAIL = "Ver detalle"

# Detail
PICK_ITEM = "Elegí un repuesto"
SORT_LABEL = "Ordenar por"
SORT_PRICE = "Precio"
SORT_DISTANCE = "Distancia"
FAIR_PRICE_PREFIX = "Precio justo:"
FAIR_PRICE_RANGE = "entre {low} y {high} ({count} locales)"
SINGLE_STORE_NOTE = "un solo local con este repuesto — no hay precio de mercado"
OUTLIER_WARNING = (
    "precio muy bajo o muy alto para el grupo — revisar: puede ser error, "
    "calidad mal etiquetada o una oferta real"
)
LOW_CONFIDENCE_WARNING = "revisar: puede ser otro modelo"
MARGIN_HEADER = "Márgenes por reparación"
MARGIN_LINE = "{label} ({service}): {verb} {amount} con el repuesto de {store} ({tier})"
MARGIN_VERB_GAIN = "ganás"
MARGIN_VERB_LOSS = "perdés"
TREND_VS = "vs hace {days} días"
TREND_CHART_LABEL = "Historial de precio justo (30 días)"
TREND_CHART_DAY_COLUMN = "día"
TREND_CHART_PRICE_COLUMN = "precio justo"
NO_TREND = "sin historial para comparar"

# Distance (wired in PR 4)
FROM_SHOP = "Desde: Local Activcelu"
FROM_MY_LOCATION = "Desde: tu ubicación"
USE_MY_LOCATION = "Usar mi ubicación"
BACK_TO_SHOP = "Volver al local"
LOCATION_DENIED = (
    "No pudimos leer tu ubicación (permiso denegado). Seguimos midiendo desde el local."
)
NO_SHOP_LOCATION = "Falta configurar la ubicación del local (SHOP_LAT/SHOP_LON)."
NO_STORE_LOCATION = "—"
SHIPS_ONLY_NOTE = "solo envío"
