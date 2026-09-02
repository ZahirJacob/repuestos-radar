"""Every user-visible string of the dashboard, in Rioplatense Spanish.

One module so (a) Mo reviews Spanish in one place, (b) an English mode later
is a translation of this file, not a hunt through page code.
"""

APP_TITLE = "Repuestos Radar"

# Login
PASSWORD_LABEL = "Contraseña"
LOGIN_BUTTON = "Entrar"
WRONG_PASSWORD = "Contraseña incorrecta."
NO_PASSWORD_CONFIGURED = "Falta configurar la contraseña de la app. Avisale al equipo."

# Navigation
NAV_PRICES = "Precios"
NAV_DETAIL = "Detalle"
NAV_SETTINGS = "Ajustes"

# Freshness footer
UPDATED_PREFIX = "Actualizado:"
NO_DATA_AT_ALL = "Todavía no hay datos guardados."

# Home
BEST_PREFIX = "Mejor precio:"
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
FAIR_PRICE_RANGE = "entre {low} y {high} ({count} tiendas)"
SINGLE_STORE_NOTE = "una sola tienda tiene este repuesto — no hay precio de mercado"
OUTLIER_WARNING = (
    "precio muy alejado del resto — puede ser error, calidad mal etiquetada o una oferta real"
)
LOW_CONFIDENCE_WARNING = "puede ser otro modelo"
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
    "No pudimos usar tu ubicación (no diste permiso). Seguimos midiendo desde el local."
)
NO_SHOP_LOCATION = "Falta configurar la ubicación del local. Avisale al equipo."
NO_STORE_LOCATION = "—"
SHIPS_ONLY_NOTE = "solo envío"

# Admin — repair prices
SERVICES_HEADER = "Precios de reparaciones"
SERVICE_EDIT = "Editar"
SERVICE_SAVE = "Guardar"
SERVICE_REMOVE = "Borrar"
SERVICE_CONFIRM = "¿Seguro?"
SERVICE_CONFIRM_YES = "Sí, borrar"
SERVICE_CONFIRM_NO = "No"
SERVICE_ADD_HEADER = "Agregar reparación"
SERVICE_LABEL_FIELD = "Nombre de la reparación"
SERVICE_ITEM_FIELD = "Repuesto que lleva"
SERVICE_PRICE_FIELD = "Precio al cliente (en pesos)"
SERVICE_ADD_BUTTON = "Agregar"
SERVICE_SAVED = "Guardado."
SERVICE_UPDATED_EXISTING = (
    "Ya había una reparación con ese nombre — se actualizó con el precio y el repuesto nuevos."
)
SERVICE_REMOVED = "Borrado."
SERVICE_NOT_FOUND = "Esa reparación ya no está en la lista."
PRICE_NOT_A_NUMBER = "El precio tiene que ser un número."
PRICE_NOT_POSITIVE = "El precio tiene que ser mayor que cero."
LABEL_EMPTY = "El nombre no puede estar vacío."

# Admin — tracked parts
TRACKED_HEADER = "Repuestos vigilados"
TRACKED_ADD_HEADER = "Agregar repuesto"
TRACKED_QUERY_FIELD = "Palabras de búsqueda"
TRACKED_QUERY_HINT = (
    "Las palabras con las que se busca en las tiendas, como en el buscador de "
    'una página. Ejemplo: "modulo samsung a32".'
)
TRACKED_ADD_BUTTON = "Agregar"
TRACKED_ADDED = (
    "Se agregó. Para ver precios ya, usá «Buscar precios ahora»; "
    "si no, aparecen con la búsqueda diaria."
)
TRACKED_ALREADY = "Ese repuesto ya está en la lista."
TRACKED_STOP = "Dejar de vigilar"
TRACKED_STOP_WARNING = "El historial de precios se guarda, pero el radar deja de buscarlo cada día."
TRACKED_CONFIRM_YES = "Sí, dejar de vigilar"
TRACKED_STOPPED = "Listo — ya no se vigila."

# Admin — quick search
QUICK_SEARCH_HEADER = "Buscar precios ahora"
QUICK_SEARCH_ITEM_FIELD = "¿Qué repuesto querés buscar?"
QUICK_SEARCH_BUTTON = "Buscar precios ahora"
QUICK_SEARCH_RUNNING = "Buscando… tarda alrededor de un minuto"
QUICK_SEARCH_PROGRESS = "Consultando {name}…"
QUICK_SEARCH_DONE = "Listo — precios actualizados."
QUICK_SEARCH_CAP = "Se usaron las {cap} búsquedas de hoy. Mañana hay más."
QUICK_SEARCH_USED = "Búsquedas de hoy: {used} de {cap}"
QUICK_SEARCH_BUSY = "Ya hay una búsqueda en curso — esperá a que termine."
QUICK_SEARCH_SKIPPED_NOTE = "{names}: el radar solo busca ahí una vez por día."
QUICK_SEARCH_BLOCKED_NOTE = "{names}: no responden desde la nube, quedan fuera por ahora."
QUICK_SEARCH_NO_ITEMS = "No hay repuestos vigilados. Agregá uno abajo para poder buscar precios."
QUICK_SEARCH_SOURCE_FAILED = "No pudimos consultar {name} esta vez."
