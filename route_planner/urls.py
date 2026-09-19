"""
URL routes for route_planner app.
"""
from django.urls import path
from .views import RoutePlannerAPIView, HealthCheckAPIView, MapDemoView

urlpatterns = [
    # REST API Endpoints
    path('api/v1/route/', RoutePlannerAPIView.as_view(), name='route_planner_api'),
    path('api/v1/health/', HealthCheckAPIView.as_view(), name='health_check_api'),

    # Interactive Map Demonstration
    path('', MapDemoView.as_view(), name='map_home'),
    path('map/', MapDemoView.as_view(), name='map_demo'),
]
