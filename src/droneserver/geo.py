"""Geodesy helpers."""

import math


def haversine_distance(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """
    Calculate the distance between two GPS coordinates using the Haversine formula.

    Args:
        lat1, lon1: First point (latitude, longitude in degrees)
        lat2, lon2: Second point (latitude, longitude in degrees)

    Returns:
        Distance in meters
    """
    R = 6371000  # Earth's radius in meters

    # Convert to radians
    phi1 = math.radians(lat1)
    phi2 = math.radians(lat2)
    delta_phi = math.radians(lat2 - lat1)
    delta_lambda = math.radians(lon2 - lon1)

    # Haversine formula
    a = math.sin(delta_phi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(delta_lambda / 2) ** 2
    c = 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))

    return R * c
