"""
Fuel Data Service
Loads, normalizes, geocodes, and queries fuel price data from the provided OPIS dataset.
"""

import csv
import logging
import math
import os
from pathlib import Path
from typing import Dict, List, Optional, Tuple, Any

import numpy as np
from django.conf import settings

logger = logging.getLogger(__name__)

# Known aliases and coordinates for cities that may differ in spelling or naming conventions
CITY_ALIASES = {
    ('brookpark', 'oh'): (41.4025, -81.8243),
    ('elizabethport', 'nj'): (40.6640, -74.2107),
    ('evergreen', 'al'): (31.4338, -86.9544),
    ('henrico', 'va'): (37.5385, -77.3489),
    ('port wentworth', 'ga'): (32.1491, -81.1632),
    ('university park', 'il'): (41.4428, -87.6839),
    ('lake station', 'in'): (41.5778, -87.2681),
    ('mount vernon', 'ky'): (37.3523, -84.3408),
    ('white sulphur springs', 'wv'): (37.7962, -80.2973),
}

CANADIAN_PROVINCES = {'AB', 'BC', 'MB', 'NB', 'NL', 'NS', 'NT', 'NU', 'ON', 'PE', 'QC', 'SK', 'YT'}

EARTH_RADIUS_MILES = 3958.8


def haversine_distance_miles(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Calculates great-circle distance between two points on Earth in miles."""
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    a = (math.sin(dlat / 2.0) ** 2 +
         math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) * math.sin(dlon / 2.0) ** 2)
    c = 2.0 * math.asin(math.sqrt(max(0.0, min(1.0, a))))
    return EARTH_RADIUS_MILES * c


class FuelStationService:
    """
    Singleton service that loads and indexes OPIS truck stop fuel prices.
    Provides fast spatial filtering and route projection.
    """
    _instance: Optional['FuelStationService'] = None

    def __init__(self):
        self.stations: List[Dict[str, Any]] = []
        self.city_coords: Dict[Tuple[str, str], Tuple[float, float]] = {}
        self._loaded: bool = False

    @classmethod
    def get_instance(cls) -> 'FuelStationService':
        if cls._instance is None:
            cls._instance = cls()
            cls._instance.initialize()
        return cls._instance

    def initialize(self) -> None:
        """Initializes the city coordinates lookup and loads the fuel station dataset."""
        if self._loaded:
            return

        self._load_city_coordinates()
        self._load_fuel_stations()
        self._loaded = True

    def _find_file(self, configured_path: str, default_filename: str) -> Path:
        """Resolves file path relative to project root or falls back to data directory."""
        path = Path(configured_path)
        if path.is_file():
            return path
        
        base_dir = getattr(settings, 'BASE_DIR', Path(__file__).resolve().parent.parent.parent)
        candidates = [
            base_dir / configured_path,
            base_dir / 'data' / default_filename,
            base_dir / default_filename,
        ]
        for candidate in candidates:
            if candidate.is_file():
                return candidate
        
        raise FileNotFoundError(f"Could not locate {default_filename}. Searched: {[str(c) for c in candidates]}")

    def _load_city_coordinates(self) -> None:
        """Loads offline US cities database for coordinate resolution."""
        try:
            cities_file = self._find_file(settings.US_CITIES_PATH, 'us_cities.csv')
            logger.info("Loading US cities coordinates from: %s", cities_file)
            with open(cities_file, mode='r', encoding='utf-8') as f:
                reader = csv.DictReader(f)
                for row in reader:
                    city = row['CITY'].strip().lower()
                    state = row['STATE_CODE'].strip().upper()
                    try:
                        lat = float(row['LATITUDE'])
                        lon = float(row['LONGITUDE'])
                        self.city_coords[(city, state)] = (lat, lon)
                    except (ValueError, KeyError):
                        continue

            # Overlay aliases
            self.city_coords.update(CITY_ALIASES)
            logger.info("Loaded coordinates for %d US cities.", len(self.city_coords))
        except Exception as e:
            logger.error("Failed to load US cities database: %s", e)
            self.city_coords.update(CITY_ALIASES)

    def _load_fuel_stations(self) -> None:
        """Parses and deduplicates OPIS fuel stations dataset."""
        try:
            fuel_file = self._find_file(settings.FUEL_DATA_PATH, 'fuel-prices.csv')
            logger.info("Loading fuel price data from: %s", fuel_file)
            
            raw_count = 0
            station_dict = {}

            with open(fuel_file, mode='r', encoding='utf-8', errors='replace') as f:
                reader = csv.DictReader(f)
                for row in reader:
                    raw_count += 1
                    try:
                        opis_id = row.get('OPIS Truckstop ID', '').strip()
                        name = row.get('Truckstop Name', '').strip()
                        address = row.get('Address', '').strip()
                        city = row.get('City', '').strip()
                        state = row.get('State', '').strip().upper()
                        price_str = row.get('Retail Price', '').strip()

                        if not state or state in CANADIAN_PROVINCES:
                            continue

                        price = float(price_str)
                        if price <= 0.0:
                            continue

                        # Resolve coordinates
                        coord = self.city_coords.get((city.lower(), state))
                        if not coord:
                            # Try address fallback or city match without extra punctuation
                            clean_city = city.replace('.', '').replace('-', ' ').strip().lower()
                            coord = self.city_coords.get((clean_city, state))

                        if not coord:
                            continue

                        lat, lon = coord

                        # Deduplicate: if same OPIS ID or same name & city, keep the lowest price
                        dedup_key = (opis_id or name.lower(), city.lower(), state)
                        if dedup_key in station_dict:
                            if price < station_dict[dedup_key]['price']:
                                station_dict[dedup_key]['price'] = price
                        else:
                            station_dict[dedup_key] = {
                                'opis_id': opis_id,
                                'name': name,
                                'address': address,
                                'city': city,
                                'state': state,
                                'price': price,
                                'latitude': lat,
                                'longitude': lon,
                            }
                    except (ValueError, KeyError) as err:
                        continue

            self.stations = list(station_dict.values())
            logger.info("Processed %d raw records into %d valid, geocoded US fuel stations.", raw_count, len(self.stations))
        except Exception as e:
            logger.error("Failed to load fuel stations dataset: %s", e)
            self.stations = []

    def find_candidate_stations(
        self,
        route_coords: List[List[float]],
        total_distance_miles: float,
        max_distance_from_route_miles: Optional[float] = None
    ) -> List[Dict[str, Any]]:
        """
        Finds all candidate fuel stations within max_distance_from_route_miles of the route.
        Returns stations with their calculated route_distance_miles (distance along route from origin).
        """
        if max_distance_from_route_miles is None:
            max_distance_from_route_miles = getattr(settings, 'MAX_STATION_DISTANCE_FROM_ROUTE_MILES', 15.0)

        if not route_coords or len(route_coords) < 2 or not self.stations:
            return []

        # 1. Compute cumulative distance along the route polyline
        # route_coords is [[lon, lat], [lon, lat], ...]
        cum_dist = [0.0]
        for i in range(1, len(route_coords)):
            d = haversine_distance_miles(
                route_coords[i-1][1], route_coords[i-1][0],
                route_coords[i][1], route_coords[i][0]
            )
            cum_dist.append(cum_dist[-1] + d)

        # Scale cumulative distance so end matches total_distance_miles exactly
        if cum_dist[-1] > 0:
            scale = total_distance_miles / cum_dist[-1]
            cum_dist = [d * scale for d in cum_dist]
        else:
            cum_dist = [0.0] * len(route_coords)

        # 2. Subsample route points for vectorized distance checking (target ~1000 sample points)
        step = max(1, len(route_coords) // 1000)
        sample_indices = list(range(0, len(route_coords), step))
        if sample_indices[-1] != len(route_coords) - 1:
            sample_indices.append(len(route_coords) - 1)

        # sampled_coords: array of shape (M, 2) where col 0 is lat, col 1 is lon
        sampled_lats = np.array([route_coords[i][1] for i in sample_indices], dtype=np.float64)
        sampled_lons = np.array([route_coords[i][0] for i in sample_indices], dtype=np.float64)
        sampled_cum_dist = np.array([cum_dist[i] for i in sample_indices], dtype=np.float64)

        # 3. Bounding box spatial filter
        deg_buffer = max_distance_from_route_miles / 55.0  # ~0.27 deg for 15 miles
        min_lat = float(np.min(sampled_lats) - deg_buffer)
        max_lat = float(np.max(sampled_lats) + deg_buffer)
        min_lon = float(np.min(sampled_lons) - deg_buffer)
        max_lon = float(np.max(sampled_lons) + deg_buffer)

        # 4. Filter stations inside bounding box
        box_stations = [
            st for st in self.stations
            if min_lat <= st['latitude'] <= max_lat and min_lon <= st['longitude'] <= max_lon
        ]

        if not box_stations:
            return []

        # 5. Vectorized distance calculation from each candidate station to sampled route
        candidates: List[Dict[str, Any]] = []
        sampled_lats_rad = np.radians(sampled_lats)
        sampled_lons_rad = np.radians(sampled_lons)
        cos_sampled_lats = np.cos(sampled_lats_rad)

        for st in box_stations:
            st_lat = st['latitude']
            st_lon = st['longitude']
            st_lat_rad = math.radians(st_lat)
            st_lon_rad = math.radians(st_lon)

            dlat = sampled_lats_rad - st_lat_rad
            dlon = sampled_lons_rad - st_lon_rad

            a = (np.sin(dlat / 2.0) ** 2 +
                 math.cos(st_lat_rad) * cos_sampled_lats * (np.sin(dlon / 2.0) ** 2))
            # clamp a to [0, 1] to avoid nan in arcsin
            dists = 2.0 * EARTH_RADIUS_MILES * np.arcsin(np.sqrt(np.clip(a, 0.0, 1.0)))

            min_idx = int(np.argmin(dists))
            min_dist = float(dists[min_idx])

            if min_dist <= max_distance_from_route_miles:
                route_dist = float(sampled_cum_dist[min_idx])
                candidates.append({
                    **st,
                    'dist_to_route_miles': round(min_dist, 2),
                    'route_distance_miles': round(route_dist, 2),
                })

        # 6. Sort by distance along the route
        candidates.sort(key=lambda x: x['route_distance_miles'])

        # 7. Deduplicate nearby stations at virtually the same location (e.g. within 0.5 miles) keeping cheapest
        unique_candidates: List[Dict[str, Any]] = []
        for cand in candidates:
            if not unique_candidates:
                unique_candidates.append(cand)
            else:
                prev = unique_candidates[-1]
                if abs(cand['route_distance_miles'] - prev['route_distance_miles']) < 0.5 and cand['city'] == prev['city']:
                    if cand['price'] < prev['price']:
                        unique_candidates[-1] = cand
                else:
                    unique_candidates.append(cand)

        logger.debug("Found %d candidate stations along route (total %0.1f miles).", len(unique_candidates), total_distance_miles)
        return unique_candidates


def get_fuel_service() -> FuelStationService:
    """Returns the singleton FuelStationService instance."""
    return FuelStationService.get_instance()
