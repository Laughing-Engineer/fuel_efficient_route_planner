"""
API Views for Fuel-Efficient Route Planner
"""

import logging
from typing import Any, Dict

from django.shortcuts import render
from django.views import View
from rest_framework import status
from rest_framework.response import Response
from rest_framework.views import APIView

from .models import RouteQueryLog
from .serializers import RoutePlannerInputSerializer, RoutePlannerResponseSerializer
from .services.fuel_service import get_fuel_service
from .services.geocoding_service import (
    get_geocoding_service,
    GeocodingException,
    LocationOutsideUSAException
)
from .services.routing_service import (
    get_routing_service,
    RoutingException,
    NoRouteFoundException
)
from .services.optimization_service import (
    get_optimization_service,
    OptimizationException,
    RouteInfeasibleException
)

logger = logging.getLogger(__name__)


class RoutePlannerAPIView(APIView):
    """
    POST /api/v1/route/
    Calculates driving route, finds candidate fuel stations,
    optimizes refueling stops based on fuel prices, and returns
    total fuel expenditure and route geometry.
    """

    def post(self, request, *args, **kwargs):
        input_serializer = RoutePlannerInputSerializer(data=request.data)
        if not input_serializer.is_valid():
            return Response(
                {
                    "success": False,
                    "error": "Invalid request payload.",
                    "details": input_serializer.errors
                },
                status=status.HTTP_400_BAD_REQUEST
            )

        start_input = input_serializer.validated_data['start']
        finish_input = input_serializer.validated_data['finish']
        max_deviation = input_serializer.validated_data.get('max_distance_from_route_miles', 15.0)

        geocoding_service = get_geocoding_service()
        routing_service = get_routing_service()
        fuel_service = get_fuel_service()
        optimization_service = get_optimization_service()

        # 1. Geocode and validate Start and Finish locations
        try:
            start_loc = geocoding_service.geocode(start_input)
        except LocationOutsideUSAException as e:
            return Response(
                {"success": False, "error": f"Start location error: {str(e)}"},
                status=status.HTTP_400_BAD_REQUEST
            )
        except GeocodingException as e:
            return Response(
                {"success": False, "error": f"Failed to geocode start location: {str(e)}"},
                status=status.HTTP_400_BAD_REQUEST
            )

        try:
            finish_loc = geocoding_service.geocode(finish_input)
        except LocationOutsideUSAException as e:
            return Response(
                {"success": False, "error": f"Finish location error: {str(e)}"},
                status=status.HTTP_400_BAD_REQUEST
            )
        except GeocodingException as e:
            return Response(
                {"success": False, "error": f"Failed to geocode finish location: {str(e)}"},
                status=status.HTTP_400_BAD_REQUEST
            )

        # 2. Query single driving route from Routing API
        try:
            route_data = routing_service.get_route(
                start_lat=start_loc['latitude'],
                start_lon=start_loc['longitude'],
                finish_lat=finish_loc['latitude'],
                finish_lon=finish_loc['longitude']
            )
        except NoRouteFoundException as e:
            return Response(
                {"success": False, "error": str(e)},
                status=status.HTTP_404_NOT_FOUND
            )
        except RoutingException as e:
            return Response(
                {"success": False, "error": f"Routing API error: {str(e)}"},
                status=status.HTTP_502_BAD_GATEWAY
            )

        total_distance = route_data['distance_miles']
        geometry = route_data['geometry']

        # 3. Locate candidate fuel stations near the route geometry locally
        candidates = fuel_service.find_candidate_stations(
            route_coords=geometry,
            total_distance_miles=total_distance,
            max_distance_from_route_miles=max_deviation
        )

        # 4. Optimize fuel stops using Dynamic Programming
        try:
            opt_result = optimization_service.optimize_stops(
                candidates=candidates,
                total_distance_miles=total_distance
            )
        except RouteInfeasibleException as e:
            return Response(
                {
                    "success": False,
                    "error": str(e),
                    "route": {
                        "distance_miles": total_distance,
                        "duration_minutes": route_data['duration_minutes'],
                        "geometry": geometry
                    }
                },
                status=status.HTTP_422_UNPROCESSABLE_ENTITY
            )
        except OptimizationException as e:
            return Response(
                {"success": False, "error": f"Optimization failure: {str(e)}"},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )

        # Build final response
        response_payload: Dict[str, Any] = {
            "success": True,
            "start": start_loc,
            "finish": finish_loc,
            "route": {
                "distance_miles": total_distance,
                "duration_minutes": route_data['duration_minutes'],
                "geometry": geometry
            },
            "vehicle": opt_result["vehicle"],
            "fuel_stops": opt_result["fuel_stops"],
            "fuel": opt_result["fuel"],
            "stops_required": opt_result["stops_required"],
            "optimization_summary": opt_result.get("optimization_summary"),
            "note": opt_result.get("note")
        }

        # Optional background audit logging to database
        try:
            RouteQueryLog.objects.create(
                start_location=start_loc['name'],
                finish_location=finish_loc['name'],
                start_latitude=start_loc['latitude'],
                start_longitude=start_loc['longitude'],
                finish_latitude=finish_loc['latitude'],
                finish_longitude=finish_loc['longitude'],
                total_distance_miles=total_distance,
                total_duration_minutes=route_data['duration_minutes'],
                total_gallons=opt_result["fuel"]["total_gallons"],
                total_fuel_cost=opt_result["fuel"]["total_cost"],
                stops_count=opt_result["stops_required"]
            )
        except Exception as log_err:
            logger.warning("Could not persist RouteQueryLog (non-critical): %s", log_err)

        return Response(response_payload, status=status.HTTP_200_OK)


class HealthCheckAPIView(APIView):
    """GET /api/v1/health/ - API health check."""
    def get(self, request, *args, **kwargs):
        fuel_service = get_fuel_service()
        return Response({
            "status": "healthy",
            "service": "Fuel-Efficient Route Planner API",
            "version": "1.0.0",
            "indexed_fuel_stations": len(fuel_service.stations)
        }, status=status.HTTP_200_OK)


class MapDemoView(View):
    """GET / or /map/ - Serves interactive Leaflet.js route planner UI."""
    def get(self, request, *args, **kwargs):
        return render(request, 'index.html')
