"""
Integration tests for Route Planner API covering all 12 assessment requirements.
Uses mocked external APIs to ensure tests are fast, deterministic, and offline-capable.
"""

from unittest.mock import patch, MagicMock
from django.test import TestCase
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APIClient

from route_planner.services.routing_service import RoutingException, NoRouteFoundException


class RoutePlannerAPITests(TestCase):
    """Automated test suite verifying API requirements 1 through 12."""

    def setUp(self):
        self.client = APIClient()
        self.url = reverse('route_planner_api')
        self.health_url = reverse('health_check_api')

    # --------------------------------------------------------------------------
    # Requirement 1: Valid Route Request
    # --------------------------------------------------------------------------
    @patch('route_planner.services.routing_service.RoutingService.get_route')
    @patch('route_planner.services.geocoding_service.GeocodingService.geocode')
    def test_01_valid_route_request(self, mock_geocode, mock_route):
        """1. Valid route request returns HTTP 200 with structured JSON response."""
        mock_geocode.side_effect = [
            {"name": "Chicago, IL", "latitude": 41.8781, "longitude": -87.6298},
            {"name": "New York, NY", "latitude": 40.7128, "longitude": -74.0060}
        ]
        mock_route.return_value = {
            "distance_miles": 790.4,
            "duration_minutes": 892.0,
            "geometry": [
                [-87.6298, 41.8781],
                [-83.5438, 41.6420],  # Toledo, OH (~235 mi)
                [-80.6474, 41.0986],  # Youngstown, OH (~400 mi)
                [-74.0060, 40.7128]   # New York, NY (~790 mi)
            ]
        }

        payload = {"start": "Chicago, IL", "finish": "New York, NY"}
        response = self.client.post(self.url, payload, format='json')

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        data = response.json()
        self.assertTrue(data['success'])
        self.assertIn('start', data)
        self.assertIn('finish', data)
        self.assertIn('route', data)
        self.assertIn('vehicle', data)
        self.assertIn('fuel_stops', data)
        self.assertIn('fuel', data)
        self.assertEqual(data['vehicle']['max_range_miles'], 500.0)
        self.assertEqual(data['vehicle']['fuel_efficiency_mpg'], 10.0)
        self.assertGreater(len(data['fuel_stops']), 0)

    # --------------------------------------------------------------------------
    # Requirement 2: Invalid / Missing Start Location
    # --------------------------------------------------------------------------
    def test_02_missing_start_location(self):
        """2. Missing or blank start location returns HTTP 400."""
        # Missing key
        response1 = self.client.post(self.url, {"finish": "New York, NY"}, format='json')
        self.assertEqual(response1.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertFalse(response1.json()['success'])

        # Empty string
        response2 = self.client.post(self.url, {"start": "", "finish": "New York, NY"}, format='json')
        self.assertEqual(response2.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertFalse(response2.json()['success'])

    # --------------------------------------------------------------------------
    # Requirement 3: Invalid / Missing Finish Location
    # --------------------------------------------------------------------------
    def test_03_missing_finish_location(self):
        """3. Missing or blank finish location returns HTTP 400."""
        # Missing key
        response1 = self.client.post(self.url, {"start": "Chicago, IL"}, format='json')
        self.assertEqual(response1.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertFalse(response1.json()['success'])

        # Empty string
        response2 = self.client.post(self.url, {"start": "Chicago, IL", "finish": "   "}, format='json')
        self.assertEqual(response2.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertFalse(response2.json()['success'])

    # --------------------------------------------------------------------------
    # Requirement 4: USA-Only Validation
    # --------------------------------------------------------------------------
    def test_04_usa_only_validation(self):
        """4. Non-US locations are rejected with HTTP 400 and clear error message."""
        payload = {"start": "Toronto, Canada", "finish": "New York, NY"}
        response = self.client.post(self.url, payload, format='json')
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("outside the USA", response.json()['error'])

        payload2 = {"start": "Chicago, IL", "finish": "London, UK"}
        response2 = self.client.post(self.url, payload2, format='json')
        self.assertEqual(response2.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("outside the USA", response2.json()['error'])

    # --------------------------------------------------------------------------
    # Requirement 5: Short Route Not Requiring Fuel Stop
    # --------------------------------------------------------------------------
    @patch('route_planner.services.routing_service.RoutingService.get_route')
    @patch('route_planner.services.geocoding_service.GeocodingService.geocode')
    def test_05_short_route_zero_stops(self, mock_geocode, mock_route):
        """5. Routes <= 500 miles require 0 stops and report trip fuel."""
        mock_geocode.side_effect = [
            {"name": "Philadelphia, PA", "latitude": 40.1162, "longitude": -75.0141},
            {"name": "New York, NY", "latitude": 40.7128, "longitude": -74.0060}
        ]
        mock_route.return_value = {
            "distance_miles": 95.0,
            "duration_minutes": 110.0,
            "geometry": [[-75.0141, 40.1162], [-74.0060, 40.7128]]
        }

        payload = {"start": "Philadelphia, PA", "finish": "New York, NY"}
        response = self.client.post(self.url, payload, format='json')
        self.assertEqual(response.status_code, status.HTTP_200_OK)

        data = response.json()
        self.assertEqual(data['stops_required'], 0)
        self.assertEqual(len(data['fuel_stops']), 0)
        self.assertEqual(data['fuel']['total_gallons'], 9.5)
        self.assertEqual(data['fuel']['total_cost'], 0.0)

    # --------------------------------------------------------------------------
    # Requirement 6: Long Route Requiring Multiple Fuel Stops
    # --------------------------------------------------------------------------
    @patch('route_planner.services.fuel_service.FuelStationService.find_candidate_stations')
    @patch('route_planner.services.routing_service.RoutingService.get_route')
    @patch('route_planner.services.geocoding_service.GeocodingService.geocode')
    def test_06_long_route_multiple_stops(self, mock_geocode, mock_route, mock_candidates):
        """6. Routes > 1000 miles require multiple refueling stops."""
        mock_geocode.side_effect = [
            {"name": "Chicago, IL", "latitude": 41.8781, "longitude": -87.6298},
            {"name": "Miami, FL", "latitude": 25.7617, "longitude": -80.1918}
        ]
        mock_route.return_value = {
            "distance_miles": 1380.0,
            "duration_minutes": 1200.0,
            "geometry": [[-87.6298, 41.8781], [-80.1918, 25.7617]]
        }
        # Provide candidates at reachable intervals: 400 mi, 800 mi, 1200 mi
        mock_candidates.return_value = [
            {"name": "Station Nashville", "city": "Nashville", "state": "TN", "latitude": 36.16, "longitude": -86.78, "route_distance_miles": 420.0, "price": 3.05, "dist_to_route_miles": 2.0},
            {"name": "Station Atlanta", "city": "Atlanta", "state": "GA", "latitude": 33.74, "longitude": -84.38, "route_distance_miles": 670.0, "price": 2.95, "dist_to_route_miles": 1.5},
            {"name": "Station Lake City", "city": "Lake City", "state": "FL", "latitude": 30.18, "longitude": -82.63, "route_distance_miles": 990.0, "price": 3.15, "dist_to_route_miles": 3.0},
        ]

        payload = {"start": "Chicago, IL", "finish": "Miami, FL"}
        response = self.client.post(self.url, payload, format='json')
        self.assertEqual(response.status_code, status.HTTP_200_OK)

        data = response.json()
        self.assertGreaterEqual(data['stops_required'], 2)
        # Verify all legs <= 500 miles
        prev_mile = 0.0
        for stop in data['fuel_stops']:
            leg = stop['route_distance_miles'] - prev_mile
            self.assertLessEqual(leg, 500.0)
            prev_mile = stop['route_distance_miles']
        self.assertLessEqual(1380.0 - prev_mile, 500.0)

    # --------------------------------------------------------------------------
    # Requirement 7: Fuel Calculation Accuracy (10 MPG)
    # --------------------------------------------------------------------------
    @patch('route_planner.services.fuel_service.FuelStationService.find_candidate_stations')
    @patch('route_planner.services.routing_service.RoutingService.get_route')
    @patch('route_planner.services.geocoding_service.GeocodingService.geocode')
    def test_07_fuel_calculation(self, mock_geocode, mock_route, mock_candidates):
        """7. Total fuel exactly equals route distance / 10 MPG."""
        mock_geocode.side_effect = [
            {"name": "A", "latitude": 40.0, "longitude": -85.0},
            {"name": "B", "latitude": 40.0, "longitude": -75.0}
        ]
        mock_route.return_value = {
            "distance_miles": 850.0,
            "duration_minutes": 900.0,
            "geometry": [[-85.0, 40.0], [-75.0, 40.0]]
        }
        mock_candidates.return_value = [
            {"name": "Stop A", "city": "City A", "state": "PA", "latitude": 40.0, "longitude": -80.0, "route_distance_miles": 450.0, "price": 3.00, "dist_to_route_miles": 1.0}
        ]

        response = self.client.post(self.url, {"start": "A", "finish": "B"}, format='json')
        data = response.json()
        self.assertEqual(data['fuel']['total_gallons'], 85.0)

        # Sum of gallons at stops must equal total fuel
        sum_gallons = sum(s['gallons_purchased'] for s in data['fuel_stops'])
        self.assertAlmostEqual(sum_gallons, 85.0, delta=0.05)

    # --------------------------------------------------------------------------
    # Requirement 8: Fuel Cost Calculation
    # --------------------------------------------------------------------------
    @patch('route_planner.services.fuel_service.FuelStationService.find_candidate_stations')
    @patch('route_planner.services.routing_service.RoutingService.get_route')
    @patch('route_planner.services.geocoding_service.GeocodingService.geocode')
    def test_08_fuel_cost_calculation(self, mock_geocode, mock_route, mock_candidates):
        """8. Stop cost equals gallons * unit price, and total cost is exact sum."""
        mock_geocode.side_effect = [
            {"name": "Start", "latitude": 40.0, "longitude": -85.0},
            {"name": "Finish", "latitude": 40.0, "longitude": -75.0}
        ]
        mock_route.return_value = {
            "distance_miles": 800.0,
            "duration_minutes": 800.0,
            "geometry": [[-85.0, 40.0], [-75.0, 40.0]]
        }
        mock_candidates.return_value = [
            {"name": "Mid Stop", "city": "Midtown", "state": "OH", "latitude": 40.0, "longitude": -80.0, "route_distance_miles": 400.0, "price": 3.25, "dist_to_route_miles": 0.5}
        ]

        response = self.client.post(self.url, {"start": "Start", "finish": "Finish"}, format='json')
        data = response.json()
        stop = data['fuel_stops'][0]
        self.assertEqual(stop['fuel_price_per_gallon'], 3.25)
        self.assertEqual(stop['gallons_purchased'], 80.0)
        self.assertEqual(stop['fuel_cost'], round(80.0 * 3.25, 2))
        self.assertEqual(data['fuel']['total_cost'], stop['fuel_cost'])

    # --------------------------------------------------------------------------
    # Requirement 9: Vehicle 500-Mile Range Constraint
    # --------------------------------------------------------------------------
    @patch('route_planner.services.fuel_service.FuelStationService.find_candidate_stations')
    @patch('route_planner.services.routing_service.RoutingService.get_route')
    @patch('route_planner.services.geocoding_service.GeocodingService.geocode')
    def test_09_vehicle_500_mile_range_constraint(self, mock_geocode, mock_route, mock_candidates):
        """9. No single travel segment exceeds 500 miles."""
        mock_geocode.side_effect = [
            {"name": "S", "latitude": 40.0, "longitude": -90.0},
            {"name": "F", "latitude": 40.0, "longitude": -70.0}
        ]
        mock_route.return_value = {
            "distance_miles": 1200.0,
            "duration_minutes": 1100.0,
            "geometry": [[-90.0, 40.0], [-70.0, 40.0]]
        }
        # Stations at 400 mi and 800 mi for a 1200-mi route
        mock_candidates.return_value = [
            {"name": "Stop 1", "city": "C1", "state": "IN", "latitude": 40.0, "longitude": -85.0, "route_distance_miles": 400.0, "price": 3.10, "dist_to_route_miles": 1.0},
            {"name": "Stop 2", "city": "C2", "state": "PA", "latitude": 40.0, "longitude": -75.0, "route_distance_miles": 800.0, "price": 3.20, "dist_to_route_miles": 1.0}
        ]

        response = self.client.post(self.url, {"start": "S", "finish": "F"}, format='json')
        data = response.json()
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        stops = data['fuel_stops']
        self.assertEqual(len(stops), 2)
        
        # Check start to stop 1 <= 500
        self.assertLessEqual(stops[0]['route_distance_miles'], 500.0)
        # Check stop 1 to stop 2 <= 500 (800 - 400 = 400 <= 500)
        self.assertLessEqual(stops[1]['route_distance_miles'] - stops[0]['route_distance_miles'], 500.0)
        # Check stop 2 to destination <= 500 (1200 - 800 = 400 <= 500)
        self.assertLessEqual(1200.0 - stops[1]['route_distance_miles'], 500.0)

    # --------------------------------------------------------------------------
    # Requirement 10: Fuel Station Selection (Price Preference)
    # --------------------------------------------------------------------------
    @patch('route_planner.services.fuel_service.FuelStationService.find_candidate_stations')
    @patch('route_planner.services.routing_service.RoutingService.get_route')
    @patch('route_planner.services.geocoding_service.GeocodingService.geocode')
    def test_10_fuel_station_selection_prefers_cheaper(self, mock_geocode, mock_route, mock_candidates):
        """10. Optimization selects lower priced stations when route remains feasible."""
        mock_geocode.side_effect = [
            {"name": "Start", "latitude": 41.0, "longitude": -88.0},
            {"name": "Finish", "latitude": 41.0, "longitude": -74.0}
        ]
        mock_route.return_value = {
            "distance_miles": 750.0,
            "duration_minutes": 750.0,
            "geometry": [[-88.0, 41.0], [-74.0, 41.0]]
        }
        # Two stations both feasible for 1-stop trip (in range 250..500)
        # Expensive: $3.99, Cheap: $2.85
        mock_candidates.return_value = [
            {"name": "Pricy Petrol", "city": "City A", "state": "IN", "latitude": 41.0, "longitude": -83.0, "route_distance_miles": 350.0, "price": 3.99, "dist_to_route_miles": 1.0},
            {"name": "Discount Diesel", "city": "City B", "state": "OH", "latitude": 41.0, "longitude": -82.0, "route_distance_miles": 380.0, "price": 2.85, "dist_to_route_miles": 1.0},
        ]

        response = self.client.post(self.url, {"start": "Start", "finish": "Finish"}, format='json')
        data = response.json()
        self.assertEqual(data['stops_required'], 1)
        selected = data['fuel_stops'][0]
        self.assertEqual(selected['name'], "Discount Diesel")
        self.assertEqual(selected['fuel_price_per_gallon'], 2.85)

    # --------------------------------------------------------------------------
    # Requirement 11: Destination Reachability
    # --------------------------------------------------------------------------
    @patch('route_planner.services.fuel_service.FuelStationService.find_candidate_stations')
    @patch('route_planner.services.routing_service.RoutingService.get_route')
    @patch('route_planner.services.geocoding_service.GeocodingService.geocode')
    def test_11_destination_reachability_guaranteed(self, mock_geocode, mock_route, mock_candidates):
        """11. Final stop is within 500 miles of the destination."""
        mock_geocode.side_effect = [
            {"name": "A", "latitude": 41.0, "longitude": -88.0},
            {"name": "B", "latitude": 41.0, "longitude": -74.0}
        ]
        mock_route.return_value = {
            "distance_miles": 800.0,
            "duration_minutes": 800.0,
            "geometry": [[-88.0, 41.0], [-74.0, 41.0]]
        }
        # Station at 200 mi only cannot reach destination (800 - 200 = 600 > 500)
        # Must pick station that allows reaching destination
        mock_candidates.return_value = [
            {"name": "Early Stop", "city": "C1", "state": "IN", "latitude": 41.0, "longitude": -86.0, "route_distance_miles": 200.0, "price": 2.50, "dist_to_route_miles": 1.0},
            {"name": "Viable Stop", "city": "C2", "state": "OH", "latitude": 41.0, "longitude": -82.0, "route_distance_miles": 420.0, "price": 3.10, "dist_to_route_miles": 1.0}
        ]

        response = self.client.post(self.url, {"start": "A", "finish": "B"}, format='json')
        data = response.json()
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        last_stop = data['fuel_stops'][-1]
        dist_to_destination = 800.0 - last_stop['route_distance_miles']
        self.assertLessEqual(dist_to_destination, 500.0)

    # --------------------------------------------------------------------------
    # Requirement 12: External API Failure Handling
    # --------------------------------------------------------------------------
    @patch('route_planner.services.routing_service.RoutingService.get_route')
    @patch('route_planner.services.geocoding_service.GeocodingService.geocode')
    def test_12_routing_api_failure_handling(self, mock_geocode, mock_route):
        """12. External routing failures return clean HTTP 502 error JSON."""
        mock_geocode.side_effect = [
            {"name": "Chicago, IL", "latitude": 41.8781, "longitude": -87.6298},
            {"name": "New York, NY", "latitude": 40.7128, "longitude": -74.0060}
        ]
        mock_route.side_effect = RoutingException("OSRM server connection timeout")

        response = self.client.post(self.url, {"start": "Chicago, IL", "finish": "New York, NY"}, format='json')
        self.assertEqual(response.status_code, status.HTTP_502_BAD_GATEWAY)
        self.assertFalse(response.json()['success'])
        self.assertIn("Routing API error", response.json()['error'])

    @patch('route_planner.services.routing_service.RoutingService.get_route')
    @patch('route_planner.services.geocoding_service.GeocodingService.geocode')
    def test_12_no_route_found_handling(self, mock_geocode, mock_route):
        """12b. When no drivable path exists (e.g. island without ferry), returns HTTP 404."""
        mock_geocode.side_effect = [
            {"name": "Miami, FL", "latitude": 25.76, "longitude": -80.19},
            {"name": "Honolulu, HI", "latitude": 21.30, "longitude": -157.85}
        ]
        mock_route.side_effect = NoRouteFoundException("No drivable route found between the specified locations.")

        response = self.client.post(self.url, {"start": "Miami, FL", "finish": "Honolulu, HI"}, format='json')
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)
        self.assertFalse(response.json()['success'])

    # --------------------------------------------------------------------------
    # Health Check Test
    # --------------------------------------------------------------------------
    def test_health_check_endpoint(self):
        """Verifies GET /api/v1/health/ returns healthy status."""
        response = self.client.get(self.health_url)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        data = response.json()
        self.assertEqual(data['status'], 'healthy')
        self.assertGreater(data['indexed_fuel_stations'], 5000)
