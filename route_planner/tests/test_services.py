"""
Unit tests for route planner services:
FuelStationService, GeocodingService, RoutingService, and OptimizationService.
"""

import requests
from unittest.mock import patch, MagicMock
from django.test import TestCase
from django.core.cache import cache

from route_planner.services.fuel_service import (
    FuelStationService,
    haversine_distance_miles,
    get_fuel_service
)
from route_planner.services.geocoding_service import (
    GeocodingService,
    GeocodingException,
    LocationOutsideUSAException,
    get_geocoding_service
)
from route_planner.services.routing_service import (
    RoutingService,
    RoutingException,
    NoRouteFoundException,
    get_routing_service
)
from route_planner.services.optimization_service import (
    OptimizationService,
    RouteInfeasibleException,
    get_optimization_service
)


class FuelServiceTests(TestCase):
    """Tests for FuelStationService loading and candidate projection."""

    def setUp(self):
        self.fuel_service = get_fuel_service()

    def test_stations_loaded_and_geocoded(self):
        """Verifies that fuel stations are loaded from the OPIS dataset and geocoded."""
        self.assertGreater(len(self.fuel_service.stations), 5000)
        sample = self.fuel_service.stations[0]
        self.assertIn('name', sample)
        self.assertIn('city', sample)
        self.assertIn('state', sample)
        self.assertIn('price', sample)
        self.assertIn('latitude', sample)
        self.assertIn('longitude', sample)
        self.assertGreater(sample['price'], 0.0)

    def test_haversine_distance_calculation(self):
        """Verifies great circle haversine distance between known coordinates."""
        # Chicago (41.8781, -87.6298) to New York (40.7128, -74.0060) is ~711 air miles
        dist = haversine_distance_miles(41.8781, -87.6298, 40.7128, -74.0060)
        self.assertAlmostEqual(dist, 711.0, delta=15.0)

    def test_candidate_stations_filtering(self):
        """Verifies finding candidate stations along a route segment."""
        # Route from Chicago to Gary, IN (~30 miles)
        coords = [
            [-87.6298, 41.8781],
            [-87.5000, 41.7000],
            [-87.3464, 41.5934]
        ]
        candidates = self.fuel_service.find_candidate_stations(
            route_coords=coords,
            total_distance_miles=30.0,
            max_distance_from_route_miles=15.0
        )
        self.assertIsInstance(candidates, list)
        for c in candidates:
            self.assertIn('route_distance_miles', c)
            self.assertIn('dist_to_route_miles', c)
            self.assertLessEqual(c['dist_to_route_miles'], 15.0)


class GeocodingServiceTests(TestCase):
    """Tests for GeocodingService offline lookups and USA validation."""

    def setUp(self):
        cache.clear()
        self.geocoder = get_geocoding_service()

    def test_offline_city_lookup_valid_us(self):
        """Verifies fast offline geocoding for standard US cities."""
        res = self.geocoder.geocode("Chicago, IL")
        self.assertEqual(res['name'], "Chicago, IL")
        self.assertAlmostEqual(res['latitude'], 41.87, delta=0.5)
        self.assertAlmostEqual(res['longitude'], -87.62, delta=0.5)

    def test_coordinate_dict_input(self):
        """Verifies input with explicit coordinate dictionary."""
        res = self.geocoder.geocode({"latitude": 34.0522, "longitude": -118.2437, "name": "Los Angeles, CA"})
        self.assertEqual(res['name'], "Los Angeles, CA")
        self.assertAlmostEqual(res['latitude'], 34.0522)
        self.assertAlmostEqual(res['longitude'], -118.2437)

    def test_coordinate_list_input(self):
        """Verifies input with [lat, lon] list."""
        res = self.geocoder.geocode([29.7604, -95.3698])
        self.assertAlmostEqual(res['latitude'], 29.7604)
        self.assertAlmostEqual(res['longitude'], -95.3698)

    def test_rejection_of_foreign_locations(self):
        """Verifies rejection of non-US locations."""
        with self.assertRaises(LocationOutsideUSAException):
            self.geocoder.geocode("Toronto, Ontario, Canada")

        with self.assertRaises(LocationOutsideUSAException):
            self.geocoder.geocode("Paris, France")

    def test_rejection_of_foreign_coordinates(self):
        """Verifies coordinates outside US territory are rejected."""
        # Paris coordinates: 48.8566, 2.3522 (longitude > -65.0)
        with self.assertRaises(LocationOutsideUSAException):
            self.geocoder.geocode({"latitude": 48.8566, "longitude": 2.3522})

    def test_empty_input_raises_exception(self):
        """Verifies empty or blank inputs raise GeocodingException."""
        with self.assertRaises(GeocodingException):
            self.geocoder.geocode("")
        with self.assertRaises(GeocodingException):
            self.geocoder.geocode("   ")


class RoutingServiceTests(TestCase):
    """Tests for RoutingService with mocked OSRM calls."""

    def setUp(self):
        cache.clear()
        self.routing = get_routing_service()

    @patch('requests.get')
    def test_successful_route_response(self, mock_get):
        """Verifies parsing of successful OSRM route response."""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "code": "Ok",
            "routes": [
                {
                    "distance": 1272000.0,  # ~790.38 miles
                    "duration": 53500.0,   # ~891.7 minutes
                    "geometry": {
                        "coordinates": [
                            [-87.6298, 41.8781],
                            [-80.6474, 41.0986],
                            [-74.0060, 40.7128]
                        ]
                    }
                }
            ]
        }
        mock_get.return_value = mock_response

        route = self.routing.get_route(41.8781, -87.6298, 40.7128, -74.0060)
        self.assertAlmostEqual(route['distance_miles'], 790.38, delta=0.5)
        self.assertAlmostEqual(route['duration_minutes'], 891.7, delta=0.5)
        self.assertEqual(len(route['geometry']), 3)

    @patch('requests.get')
    def test_no_route_found_raises_exception(self, mock_get):
        """Verifies NoRoute response raises NoRouteFoundException."""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"code": "NoRoute", "routes": []}
        mock_get.return_value = mock_response

        with self.assertRaises(NoRouteFoundException):
            self.routing.get_route(0.0, 0.0, 1.0, 1.0)

    @patch('requests.get')
    def test_network_failure_raises_routing_exception(self, mock_get):
        """Verifies network errors raise RoutingException."""
        mock_get.side_effect = requests.RequestException("Connection timeout")
        with self.assertRaises(RoutingException):
            self.routing.get_route(41.8781, -87.6298, 40.7128, -74.0060)


class OptimizationServiceTests(TestCase):
    """Tests for the Dynamic Programming fuel-stop optimization service."""

    def setUp(self):
        self.optimizer = get_optimization_service()

    def test_short_route_requires_zero_stops(self):
        """Verifies routes <= 500 miles require 0 stops and report trip fuel."""
        result = self.optimizer.optimize_stops(candidates=[], total_distance_miles=350.0)
        self.assertEqual(result['stops_required'], 0)
        self.assertEqual(len(result['fuel_stops']), 0)
        self.assertEqual(result['fuel']['total_gallons'], 35.0)
        self.assertEqual(result['fuel']['total_cost'], 0.0)

    def test_medium_route_selects_cheapest_stop(self):
        """Verifies a 700-mile route picks the cheapest reachable station."""
        candidates = [
            {
                "name": "Expensive Station A",
                "city": "City A",
                "state": "IN",
                "latitude": 41.5,
                "longitude": -86.0,
                "route_distance_miles": 350.0,
                "price": 3.99
            },
            {
                "name": "Cheap Station B",
                "city": "City B",
                "state": "OH",
                "latitude": 41.2,
                "longitude": -82.5,
                "route_distance_miles": 360.0,
                "price": 2.89
            }
        ]
        result = self.optimizer.optimize_stops(candidates=candidates, total_distance_miles=700.0)
        self.assertEqual(result['stops_required'], 1)
        self.assertEqual(result['fuel_stops'][0]['name'], "Cheap Station B")
        self.assertEqual(result['fuel_stops'][0]['fuel_price_per_gallon'], 2.89)
        self.assertEqual(result['fuel']['total_gallons'], 70.0)
        self.assertEqual(result['fuel']['total_cost'], round(70.0 * 2.89, 2))

    def test_multi_stop_route_adheres_to_500_mile_range(self):
        """Verifies that on a 1,200-mile route, no leg exceeds 500 miles."""
        candidates = [
            {"name": "Stop 1", "city": "C1", "state": "IL", "latitude": 41.0, "longitude": -88.0, "route_distance_miles": 400.0, "price": 3.10},
            {"name": "Stop 2", "city": "C2", "state": "OH", "latitude": 40.0, "longitude": -83.0, "route_distance_miles": 800.0, "price": 3.05},
        ]
        result = self.optimizer.optimize_stops(candidates=candidates, total_distance_miles=1200.0)
        self.assertEqual(result['stops_required'], 2)
        
        # Verify leg distances
        stop1_dist = result['fuel_stops'][0]['route_distance_miles']
        stop2_dist = result['fuel_stops'][1]['route_distance_miles']
        self.assertLessEqual(stop1_dist, 500.0)
        self.assertLessEqual(stop2_dist - stop1_dist, 500.0)
        self.assertLessEqual(1200.0 - stop2_dist, 500.0)

        # Verify fuel accounting
        self.assertEqual(result['fuel']['total_gallons'], 120.0)
        sum_purchased = sum(s['gallons_purchased'] for s in result['fuel_stops'])
        self.assertAlmostEqual(sum_purchased, 120.0, delta=0.1)

    def test_infeasible_route_raises_exception(self):
        """Verifies that a route with an unbridgeable gap (>500 mi) raises RouteInfeasibleException."""
        # 1200-mile route with only one station at mile 700 (cannot reach mile 700 from 0!)
        candidates = [
            {"name": "Isolated Station", "city": "Nowhere", "state": "NE", "latitude": 41.0, "longitude": -100.0, "route_distance_miles": 700.0, "price": 3.00}
        ]
        with self.assertRaises(RouteInfeasibleException):
            self.optimizer.optimize_stops(candidates=candidates, total_distance_miles=1200.0)
