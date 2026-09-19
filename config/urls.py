"""
URL configuration for fuel_efficient_route_planner project.
"""
from django.contrib import admin
from django.urls import path, include

urlpatterns = [
    path('admin/', admin.site.urls),
    path('', include('route_planner.urls')),
]
