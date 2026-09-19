"""
Geocoding Service
Resolves user location strings or coordinates into validated US geographic points.
Uses offline city database for zero-latency lookup and Nominatim as fallback with caching.
"""

import logging
import re
from typing import Any, Dict, Optional, Tuple, Union

import requests
from django.conf import settings
from django.core.cache import cache

from .fuel_service import get_fuel_service

logger = logging.getLogger(__name__)

# Bounding box for US territories (Contiguous US, Alaska, Hawaii)
US_MIN_LAT = 18.0
US_MAX_LAT = 72.0
US_MIN_LON = -179.5
US_MAX_LON = -65.0

# 50 US States + DC postal codes
US_STATES = {
    'AL', 'AK', 'AZ', 'AR', 'CA', 'CO', 'CT', 'DE', 'FL', 'GA',
    'HI', 'ID', 'IL', 'IN', 'IA', 'KS', 'KY', 'LA', 'ME', 'MD',
    'MA', 'MI', 'MN', 'MS', 'MO', 'MT', 'NE', 'NV', 'NH', 'NJ',
    'NM', 'NY', 'NC', 'ND', 'OH', 'OK', 'OR', 'PA', 'RI', 'SC',
    'SD', 'TN', 'TX', 'UT', 'VT', 'VA', 'WA', 'WV', 'WI', 'WY', 'DC'
}


class GeocodingException(Exception):
    """Base exception for geocoding failures."""
    pass


class LocationOutsideUSAException(GeocodingException):
    """Raised when a location is determined to be outside the United States."""
    pass


class GeocodingService:
    """Service for resolving and validating locations within the United States."""

    def __init__(self):
        self.base_url = getattr(settings, 'NOMINATIM_BASE_URL', 'https://nominatim.openstreetmap.org')
        self.user_agent = getattr(settings, 'NOMINATIM_USER_AGENT', 'SpotterFuelRoutePlanner/1.0')
        self.timeout = 10

    def geocode(self, location_input: Union[str, Dict[str, Any], list, tuple]) -> Dict[str, Any]:
        """
        Geocodes and validates a start or finish location.
        Accepts:
          - String: "Chicago, IL", "New York, NY", "350 5th Ave, New York, NY"
          - Dict: {"latitude": 41.8781, "longitude": -87.6298, "name": "Chicago, IL"}
          - List/Tuple: [41.8781, -87.6298]
        Returns:
          {
            "name": "Chicago, IL",
            "latitude": 41.8781,
            "longitude": -87.6298
          }
        """
        if not location_input:
            raise GeocodingException("Location cannot be empty.")

        # Case 1: Coordinate dictionary
        if isinstance(location_input, dict):
            lat = location_input.get('latitude') or location_input.get('lat')
            lon = location_input.get('longitude') or location_input.get('lon') or location_input.get('lng')
            name = location_input.get('name') or f"{lat}, {lon}"
            if lat is not None and lon is not None:
                try:
                    lat_f = float(lat)
                    lon_f = float(lon)
                    self._validate_us_coordinates(lat_f, lon_f, name)
                    return {
                        "name": str(name),
                        "latitude": round(lat_f, 6),
                        "longitude": round(lon_f, 6)
                    }
                except ValueError:
                    raise GeocodingException(f"Invalid coordinate format: {location_input}")

        # Case 2: Coordinate list or tuple [lat, lon]
        if isinstance(location_input, (list, tuple)) and len(location_input) >= 2:
            try:
                lat_f = float(location_input[0])
                lon_f = float(location_input[1])
                self._validate_us_coordinates(lat_f, lon_f, f"{lat_f}, {lon_f}")
                return {
                    "name": f"{lat_f:.4f}, {lon_f:.4f}",
                    "latitude": round(lat_f, 6),
                    "longitude": round(lon_f, 6)
                }
            except (ValueError, TypeError):
                raise GeocodingException(f"Invalid coordinate list: {location_input}")

        # Case 3: Text query
        if not isinstance(location_input, str):
            raise GeocodingException(f"Unsupported location format: {type(location_input).__name__}")

        query = location_input.strip()
        if not query:
            raise GeocodingException("Location query string cannot be blank.")

        import hashlib
        # Sanitize cache key for memcached/redis safety
        cache_hash = hashlib.md5(query.lower().encode('utf-8')).hexdigest()
        cache_key = f"geocode_{cache_hash}"
        cached = cache.get(cache_key)
        if cached:
            return cached

        # Fast offline check for "City, ST" patterns (e.g. "Chicago, IL")
        offline_result = self._try_offline_city_lookup(query)
        if offline_result:
            cache.set(cache_key, offline_result, timeout=getattr(settings, 'CACHE_TTL_SECONDS', 86400))
            return offline_result

        # Check if query matches Canadian or international format directly
        self._check_disallowed_foreign_pattern(query)

        # Fallback to Nominatim geocoding
        result = self._geocode_via_nominatim(query)
        cache.set(cache_key, result, timeout=getattr(settings, 'CACHE_TTL_SECONDS', 86400))
        return result

    def _try_offline_city_lookup(self, query: str) -> Optional[Dict[str, Any]]:
        """Checks if the query matches a known US city and state in the offline database."""
        # Match "City, ST" or "City, State"
        match = re.match(r"^([A-Za-z\s\.\-]+),\s*([A-Za-z]{2})$", query)
        if match:
            city = match.group(1).strip().lower()
            state = match.group(2).strip().upper()

            if state in US_STATES:
                fuel_service = get_fuel_service()
                coords = fuel_service.city_coords.get((city, state))
                if coords:
                    return {
                        "name": f"{match.group(1).strip()}, {state}",
                        "latitude": round(coords[0], 6),
                        "longitude": round(coords[1], 6)
                    }
        return None

    def _check_disallowed_foreign_pattern(self, query: str) -> None:
        """Quick rejection of obvious international inputs before making API calls."""
        # Non-US states/provinces/countries
        foreign_indicators = [
            'canada', 'mexico', 'toronto', 'vancouver', 'montreal', 'ontario',
            'quebec', 'alberta', 'british columbia', 'london', 'uk', 'france', 'paris',
            'germany', 'berlin', 'tokyo', 'japan', 'india', 'australia'
        ]
        q_lower = query.lower()
        for indicator in foreign_indicators:
            if indicator in q_lower:
                raise LocationOutsideUSAException(f"Location '{query}' is outside the USA. Locations must be within the United States.")

    def _geocode_via_nominatim(self, query: str) -> Dict[str, Any]:
        """Queries OpenStreetMap Nominatim with USA country restriction."""
        url = f"{self.base_url}/search"
        params = {
            'q': query,
            'format': 'json',
            'addressdetails': 1,
            'countrycodes': 'us',
            'limit': 1
        }
        headers = {'User-Agent': self.user_agent}

        try:
            response = requests.get(url, params=params, headers=headers, timeout=self.timeout)
            if response.status_code != 200:
                raise GeocodingException(f"Geocoding service error (HTTP {response.status_code}).")

            data = response.json()
            if not data:
                # Query without countrycode to detect if it was outside USA
                fallback_resp = requests.get(url, params={'q': query, 'format': 'json', 'limit': 1}, headers=headers, timeout=self.timeout)
                if fallback_resp.status_code == 200 and fallback_resp.json():
                    raise LocationOutsideUSAException(f"Location '{query}' is outside the USA. Both locations must be within the United States.")
                raise GeocodingException(f"Could not resolve location: '{query}'. Please verify spelling.")

            item = data[0]
            lat = float(item['lat'])
            lon = float(item['lon'])
            display_name = item.get('display_name', query)

            self._validate_us_coordinates(lat, lon, query)

            # Extract clean city/state name if available
            address = item.get('address', {})
            city = address.get('city') or address.get('town') or address.get('village') or address.get('hamlet')
            state = address.get('state')
            formatted_name = f"{city}, {state}" if (city and state) else display_name.split(',')[0] + ', USA'

            return {
                "name": formatted_name,
                "latitude": round(lat, 6),
                "longitude": round(lon, 6)
            }
        except requests.RequestException as e:
            logger.error("Nominatim request failed for query '%s': %s", query, e)
            raise GeocodingException(f"Geocoding network request failed: {str(e)}")

    def _validate_us_coordinates(self, lat: float, lon: float, location_name: str) -> None:
        """Validates that coordinates are within US geographical boundaries."""
        if not (US_MIN_LAT <= lat <= US_MAX_LAT and US_MIN_LON <= lon <= US_MAX_LON):
            raise LocationOutsideUSAException(
                f"Location '{location_name}' ({lat}, {lon}) is outside the USA boundaries."
            )


def get_geocoding_service() -> GeocodingService:
    """Factory function for GeocodingService."""
    return GeocodingService()
