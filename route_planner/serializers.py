"""
Serializers for Route Planner API requests and responses.
"""

from rest_framework import serializers


class RoutePlannerInputSerializer(serializers.Serializer):
    """
    Validates the input parameters for route planning and fuel stop optimization.
    Accepts place names (e.g. "Chicago, IL") or coordinate structures.
    """
    start = serializers.JSONField(
        required=True,
        help_text="Start location name (e.g. 'Chicago, IL') or coordinate object {'latitude': 41.8781, 'longitude': -87.6298}"
    )
    finish = serializers.JSONField(
        required=True,
        help_text="Finish location name (e.g. 'New York, NY') or coordinate object {'latitude': 40.7128, 'longitude': -74.0060}"
    )
    max_distance_from_route_miles = serializers.FloatField(
        required=False,
        default=15.0,
        min_value=1.0,
        max_value=100.0,
        help_text="Maximum allowed distance in miles for a fuel station from the highway route (default: 15.0)"
    )

    def validate_start(self, value):
        if not value:
            raise serializers.ValidationError("Start location cannot be empty.")
        if isinstance(value, str) and not value.strip():
            raise serializers.ValidationError("Start location cannot be blank.")
        return value

    def validate_finish(self, value):
        if not value:
            raise serializers.ValidationError("Finish location cannot be empty.")
        if isinstance(value, str) and not value.strip():
            raise serializers.ValidationError("Finish location cannot be blank.")
        return value


class LocationOutputSerializer(serializers.Serializer):
    name = serializers.CharField()
    latitude = serializers.FloatField()
    longitude = serializers.FloatField()


class RouteOutputSerializer(serializers.Serializer):
    distance_miles = serializers.FloatField()
    duration_minutes = serializers.FloatField()
    geometry = serializers.ListField(
        child=serializers.ListField(child=serializers.FloatField())
    )


class VehicleOutputSerializer(serializers.Serializer):
    max_range_miles = serializers.FloatField()
    fuel_efficiency_mpg = serializers.FloatField()
    tank_capacity_gallons = serializers.FloatField()


class FuelStopOutputSerializer(serializers.Serializer):
    stop_number = serializers.IntegerField()
    opis_id = serializers.CharField(allow_blank=True)
    name = serializers.CharField()
    address = serializers.CharField(allow_blank=True)
    city = serializers.CharField()
    state = serializers.CharField()
    latitude = serializers.FloatField()
    longitude = serializers.FloatField()
    route_distance_miles = serializers.FloatField()
    distance_from_route_miles = serializers.FloatField(required=False)
    fuel_price_per_gallon = serializers.FloatField()
    gallons_purchased = serializers.FloatField()
    fuel_cost = serializers.FloatField()


class FuelSummaryOutputSerializer(serializers.Serializer):
    total_gallons = serializers.FloatField()
    total_cost = serializers.FloatField()


class RoutePlannerResponseSerializer(serializers.Serializer):
    success = serializers.BooleanField(default=True)
    start = LocationOutputSerializer()
    finish = LocationOutputSerializer()
    route = RouteOutputSerializer()
    vehicle = VehicleOutputSerializer()
    fuel_stops = serializers.ListField(child=FuelStopOutputSerializer())
    fuel = FuelSummaryOutputSerializer()
    stops_required = serializers.IntegerField()
    optimization_summary = serializers.DictField(required=False)
    note = serializers.CharField(required=False)
