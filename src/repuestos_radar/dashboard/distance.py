"""Straight-line distance: haversine + Argentine display formatting.

Deliberately simple (approved design): no routing, no maps API, no "best
option" scoring. The reference point defaults to the Activcelu shop, whose
coordinates live in the environment (SHOP_LAT / SHOP_LON — Streamlit secrets
in the cloud, .env locally) rather than this public repo.
"""

import math
import os

from dotenv import load_dotenv

_EARTH_RADIUS_KM = 6371.0


def haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Great-circle distance between two points, in kilometers."""
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlambda = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlambda / 2) ** 2
    return 2 * _EARTH_RADIUS_KM * math.asin(math.sqrt(a))


def format_distance_km(km: float) -> str:
    """ "850 m" under 1 km, "2,1 km" under 10, whole km from there up.

    The space before the unit is non-breaking: the text lands in a pill on a
    phone-width screen, which must not wrap between the number and the unit.
    Only the dashboard formats distances, so no plain-space variant exists.
    """
    meters = round(km * 1000)
    if meters < 1000:
        # Round short hops to 10 m — fake precision helps nobody.
        return f"{round(meters, -1)}\u00a0m"
    if km < 9.95:  # under this, one decimal still rounds below 10,0
        return f"{km:.1f}".replace(".", ",") + "\u00a0km"
    return f"{round(km)}\u00a0km"


def shop_location() -> tuple[float, float] | None:
    """The Activcelu shop's position from the environment, or None when unset."""
    load_dotenv()
    raw_lat, raw_lon = os.environ.get("SHOP_LAT"), os.environ.get("SHOP_LON")
    if not raw_lat or not raw_lon:
        return None
    try:
        lat, lon = float(raw_lat), float(raw_lon)
    except ValueError:
        return None
    if not (-90.0 <= lat <= 90.0 and -180.0 <= lon <= 180.0):
        return None
    return lat, lon
