from django.db import models

class RouteQueryLog(models.Model):
    """
    Optional audit log for route queries.
    Stores historical route computations, driving distances, and optimized fuel stops.
    """
    start_location = models.CharField(max_length=255)
    finish_location = models.CharField(max_length=255)
    start_latitude = models.FloatField()
    start_longitude = models.FloatField()
    finish_latitude = models.FloatField()
    finish_longitude = models.FloatField()
    total_distance_miles = models.FloatField()
    total_duration_minutes = models.FloatField()
    total_gallons = models.FloatField()
    total_fuel_cost = models.DecimalField(max_digits=10, decimal_places=2)
    stops_count = models.IntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']
        verbose_name = 'Route Query Log'
        verbose_name_plural = 'Route Query Logs'

    def __str__(self):
        return f"{self.start_location} -> {self.finish_location} ({self.total_distance_miles:.1f} mi, ${self.total_fuel_cost})"
