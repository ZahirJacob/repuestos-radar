"""Every user-visible string of the dashboard, in English.

A translation of ``text_es`` — same names, same ``{placeholders}`` — picked
by the language toggle (see ``text``). Keep the two files in step: a test
checks that every name and every placeholder matches.
"""

from repuestos_radar.report import TIER_LABELS_EN as TIER_LABELS  # noqa: F401

APP_TITLE = "Repuestos Radar"

# Login
PASSWORD_LABEL = "Password"
LOGIN_BUTTON = "Sign in"
WRONG_PASSWORD = "Wrong password."
LOGIN_THROTTLED = "Too many attempts. Wait a moment before trying again."
LOGOUT_BUTTON = "Sign out"
NO_PASSWORD_CONFIGURED = "The app password is not configured. Let the team know."
LOGIN_STATUS = "From Rosario · {count} stores on the radar"
LOGIN_STATUS_ONE = "From Rosario · 1 store on the radar"
LOGIN_STATUS_NO_COUNT = "From Rosario"

# Navigation
NAV_PRICES = "Prices"
NAV_DETAIL = "Detail"
NAV_SETTINGS = "Settings"

# Freshness footer
UPDATED_PREFIX = "Updated:"
UPDATED_TODAY = "Updated today"
NO_DATA_AT_ALL = "No data stored yet."

# Home
BEST_CAPTION = "Best price at {store} ({tier})"
MARGIN_GAIN = "You make {amount}"
MARGIN_LOSS = "You lose {amount}"
NEEDS_REVIEW_DOT = "⚠ some prices need a look"
NO_DATA_TODAY = "no data today"
SEE_DETAIL = "See detail"

# Detail
PICK_ITEM = "Pick a part"
SORT_LABEL = "Sort by"
SORT_PRICE = "Price"
SORT_DISTANCE = "Distance"
TIER_STORE_COUNT = "{count} stores"
TIER_STORE_COUNT_ONE = "1 store"
FAIR_PRICE_PREFIX = "Fair price:"
FAIR_PRICE_RANGE = "between {low} and {high} ({count} stores)"
SINGLE_STORE_NOTE = "only one store carries this part — no market price"
OUTLIER_WARNING = "far from the rest — could be a mistake or a real deal"
LOW_CONFIDENCE_WARNING = "may be a different model"
MARGIN_HEADER = "Margins per repair"
MARGIN_LINE = "{label} ({service}): {verb} {amount} with the part from {store} ({tier})"
MARGIN_VERB_GAIN = "you make"
MARGIN_VERB_LOSS = "you lose"
TREND_HEADER = "Trend"
TREND_VS = "vs {days} days ago"
TREND_CHART_LABEL = "Fair-price history (30 days)"
TREND_CHART_DAY_COLUMN = "day"
TREND_CHART_PRICE_COLUMN = "fair price"
NO_TREND = "no history to compare"

# Distance
FROM_SHOP = "From: the Activcelu shop"
FROM_MY_LOCATION = "From: your location"
USE_MY_LOCATION = "Use my location"
BACK_TO_SHOP = "Back to the shop"
LOCATION_DENIED = (
    "We could not use your location (the phone did not share it). Still measuring from the shop."
)
NO_SHOP_LOCATION = "The shop location is not configured. Let the team know."
NO_STORE_LOCATION = "—"
SHIPS_ONLY_NOTE = "shipping only"

# Admin — repair prices
SERVICES_HEADER = "Repair prices"
SERVICE_EDIT = "Edit"
SERVICE_SAVE = "Save"
SERVICE_REMOVE = "Delete"
SERVICE_CONFIRM = "Are you sure?"
SERVICE_CONFIRM_YES = "Yes, delete"
SERVICE_CONFIRM_NO = "No"
SERVICE_ADD_HEADER = "Add a repair"
SERVICE_LABEL_FIELD = "Repair name"
SERVICE_ITEM_FIELD = "Part it uses"
SERVICE_PRICE_FIELD = "Customer price (in pesos)"
SERVICE_ADD_BUTTON = "Add"
SERVICE_SAVED = "Saved."
SERVICE_UPDATED_EXISTING = (
    "A repair with that name already existed — it was updated with the new price and part."
)
SERVICE_REMOVED = "Deleted."
SERVICE_NOT_FOUND = "That repair is no longer on the list."
PRICE_NOT_A_NUMBER = "The price has to be a number."
PRICE_NOT_POSITIVE = "The price has to be greater than zero."
LABEL_EMPTY = "The name cannot be empty."

# Admin — tracked parts
TRACKED_HEADER = "Tracked parts"
TRACKED_ADD_HEADER = "Add a part or a phone"
TRACKED_QUERY_FIELD = "Search terms"
TRACKED_QUERY_HINT = (
    "The terms used to search the stores, like in a website's search box. "
    'Examples: "modulo samsung a32" or "moto g52".'
)
TRACKED_KIND_LABEL = "What is it?"
TRACKED_KIND_PART = "Part"
TRACKED_KIND_PHONE = "Phone"
TRACKED_KIND_PHONE_TAG = "phone"
TRACKED_ADD_BUTTON = "Add"
TRACKED_ADDED = (
    "Added. To see prices right away use “Search prices now”; "
    "otherwise they show up with the daily search."
)
TRACKED_ALREADY = "Already on the list."
TRACKED_STOP = "Stop tracking"
TRACKED_STOP_WARNING = (
    "The price history is kept, but the radar will no longer search for it each day."
)
TRACKED_CONFIRM_YES = "Yes, stop tracking"
TRACKED_STOPPED = "Done — no longer tracked."

# Admin — quick search
QUICK_SEARCH_HEADER = "Search prices now"
QUICK_SEARCH_ITEM_FIELD = "Which part do you want to search for?"
QUICK_SEARCH_BUTTON = "Search prices now"
QUICK_SEARCH_RUNNING = "Searching… takes about a minute"
QUICK_SEARCH_PROGRESS = "Checking {name}…"
QUICK_SEARCH_DONE = "Done — prices updated."
QUICK_SEARCH_CAP = "Today's {cap} searches are used up. There are more tomorrow."
QUICK_SEARCH_USED = "Searches today: {used} of {cap}"
QUICK_SEARCH_BUSY = "A search is already running — wait for it to finish."
QUICK_SEARCH_SKIPPED_NOTE = "{names}: the radar only searches there once a day."
QUICK_SEARCH_BLOCKED_NOTE = "{names}: the radar can't get in for now."
QUICK_SEARCH_NO_ITEMS = "No parts are being tracked. Add one below to search prices."
QUICK_SEARCH_SOURCE_FAILED = "We could not check {name} this time."

# Public demo
DEMO_BANNER = (
    "**Public demo.** The prices are generated sample data, not real store prices. "
    "Settings is read-only."
)
DEMO_LANGUAGE_LABEL = "Language"
DEMO_ADMIN_READ_ONLY = "Settings are read-only in the demo."
DEMO_QUICK_SEARCH_OFF = "Not available in the demo — it would search the real stores."
# The demo measures from a public spot, not the shop, and never names the client.
DEMO_FROM_SHOP = "From: central Rosario"
DEMO_BACK_TO_SHOP = "Back to central Rosario"
DEMO_LOCATION_DENIED = (
    "We could not use your location (the phone did not share it). "
    "Still measuring from central Rosario."
)
