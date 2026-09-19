"""
Routing Service
Interacts with the Open Source Routing Machine (OSRM) API to retrieve driving directions,
distance, duration, and GeoJSON route polyline geometry.
"""

import logging
from typing import Any, Dict, List, Tuple

import requests
from django.conf import settings
from django.core.cache import cache

logger = logging.getLogger(__name__)

METERS_TO_MILES = 1.0 / 1609.344
SECONDS_TO_MINUTES = 1.0 / 60.0


class RoutingException(Exception):
    """Base exception for routing failures."""
    pass


class NoRouteFoundException(RoutingException):
    """Raised when no drivable route exists between locations."""
    pass


class RoutingService:
    """Service for querying OSRM driving routes."""

    def __init__(self):
        self.base_url = getattr(settings, 'OSRM_BASE_URL', 'http://router.project-osrm.org')
        self.timeout = 15

    def get_route(
        self,
        start_lat: float,
        start_lon: float,
        finish_lat: float,
        finish_lon: float
    ) -> Dict[str, Any]:
        """
        Retrieves the driving route between two coordinate points.
        Returns:
          {
            "distance_miles": 790.4,
            "duration_minutes": 892.4,
            "geometry": [[lon, lat], [lon, lat], ...]
          }
        """
        import hashlib
        raw_key = f"route_{round(start_lat, 4)}_{round(start_lon, 4)}_{round(finish_lat, 4)}_{round(finish_lon, 4)}"
        cache_hash = hashlib.md5(raw_key.encode('utf-8')).hexdigest()
        cache_key = f"route_{cache_hash}"
        cached_route = cache.get(cache_key)
        if cached_route:
            logger.debug("Returning cached route for key: %s", cache_key)
            return cached_route

        url = f"{self.base_url}/route/v1/driving/{start_lon},{start_lat};{finish_lon},{finish_lat}"
        params = {
            'overview': 'full',
            'geometries': 'geojson'
        }

        try:
            logger.info("Requesting route from OSRM: (%s, %s) -> (%s, %s)", start_lat, start_lon, finish_lat, finish_lon)
            response = requests.get(url, params=params, timeout=self.timeout)

            if response.status_code != 200:
                raise RoutingException(f"Routing service returned HTTP {response.status_code}: {response.text}")

            data = response.json()
            code = data.get('code')

            if code != 'Ok' or not data.get('routes'):
                if code == 'NoRoute':
                    raise NoRouteFoundException("No drivable route found between the specified locations.")
                raise RoutingException(f"Routing failed with code: {code}")

            route = data['routes'][0]
            distance_meters = float(route.get('distance', 0.0))
            duration_seconds = float(route.get('duration', 0.0))
            geometry_coords = route.get('geometry', {}).get('coordinates', [])

            if not geometry_coords:
                raise RoutingException("Routing API did not return route geometry coordinates.")

            distance_miles = round(distance_meters * METERS_TO_MILES, 2)
            duration_minutes = round(duration_seconds * SECONDS_TO_MINUTES, 1)

            result = {
                "distance_miles": distance_miles,
                "duration_minutes": duration_minutes,
                "geometry": geometry_coords
            }

            # Cache route result
            cache.set(cache_key, result, timeout=getattr(settings, 'CACHE_TTL_SECONDS', 86400))
            return result

        except requests.RequestException as e:
            logger.error("OSRM route request failed: %s", e)
            raise RoutingException(f"External routing service communication failed: {str(e)}")


def get_routing_service() -> RoutingService:
    """Factory function for RoutingService."""
    return RoutingService()
