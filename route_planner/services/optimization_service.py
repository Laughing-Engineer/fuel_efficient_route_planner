"""
Fuel Optimization Service
Implements a Dynamic Programming / DAG Shortest Path optimization algorithm
to select optimal fuel stops along a driving route.
"""

import logging
from decimal import Decimal, ROUND_HALF_UP
from typing import Any, Dict, List, Optional

from django.conf import settings

logger = logging.getLogger(__name__)


class OptimizationException(Exception):
    """Base exception for optimization errors."""
    pass


class RouteInfeasibleException(OptimizationException):
    """Raised when no sequence of fuel stops can satisfy the vehicle range constraint."""
    pass


class OptimizationService:
    """
    Optimizes fuel stops along a driving route based on retail fuel prices,
    vehicle maximum range constraint, and fuel efficiency.
    """

    def __init__(
        self,
        max_range_miles: Optional[float] = None,
        fuel_efficiency_mpg: Optional[float] = None
    ):
        self.max_range_miles = max_range_miles or getattr(settings, 'MAX_VEHICLE_RANGE_MILES', 500.0)
        self.fuel_efficiency_mpg = fuel_efficiency_mpg or getattr(settings, 'FUEL_EFFICIENCY_MPG', 10.0)

    def optimize_stops(
        self,
        candidates: List[Dict[str, Any]],
        total_distance_miles: float
    ) -> Dict[str, Any]:
        """
        Selects optimal fuel stops along the route to minimize total fuel expenditure
        while strictly adhering to the vehicle's 500-mile range limit.

        Assumptions (documented):
          1. Maximum Range: 500 miles on a full tank (50 gallons at 10 MPG).
          2. Fuel Efficiency: exactly 10 miles per gallon.
          3. Starting Fuel: The vehicle starts with a full tank (500 miles range).
          4. If total route distance <= 500 miles:
             No fuel stops are required. The trip is completed on the initial tank.
             Total gallons consumed = total_distance / 10.
          5. If total route distance > 500 miles:
             The vehicle must refuel at one or more truck stops.
             The sequence of stops is optimized via Dynamic Programming on a DAG,
             where edges represent reachable travel segments (<= 500 miles) and
             edge weights correspond to fuel cost at optimal retail prices.
             Total fuel purchased across selected stops equals total route consumption (D / 10).
        """
        total_distance = float(total_distance_miles)
        total_gallons = round(total_distance / self.fuel_efficiency_mpg, 2)

        # Case 1: Route is within the initial 500-mile vehicle range
        if total_distance <= self.max_range_miles:
            logger.info("Route distance (%.1f mi) <= max range (%.1f mi). 0 fuel stops needed.", total_distance, self.max_range_miles)
            return {
                "vehicle": {
                    "max_range_miles": self.max_range_miles,
                    "fuel_efficiency_mpg": self.fuel_efficiency_mpg,
                    "tank_capacity_gallons": round(self.max_range_miles / self.fuel_efficiency_mpg, 1)
                },
                "fuel_stops": [],
                "fuel": {
                    "total_gallons": total_gallons,
                    "total_cost": 0.00
                },
                "stops_required": 0,
                "note": "Short route within 500-mile range. Trip completed on initial full tank without refueling stops."
            }

        # Case 2: Route > 500 miles - Refueling stops required
        if not candidates:
            raise RouteInfeasibleException(
                f"Route distance is {total_distance:.1f} miles (exceeds {self.max_range_miles} mi range), "
                "but no candidate fuel stations were found near the route."
            )

        # Sort candidate stations by distance along the route
        sorted_candidates = sorted(candidates, key=lambda x: x['route_distance_miles'])

        # Filter out redundant stations at the exact same mile marker with higher prices
        filtered_stations: List[Dict[str, Any]] = []
        for c in sorted_candidates:
            if not filtered_stations:
                filtered_stations.append(c)
            else:
                last = filtered_stations[-1]
                if abs(last['route_distance_miles'] - c['route_distance_miles']) < 0.2 and last['name'] == c['name']:
                    if c['price'] < last['price']:
                        filtered_stations[-1] = c
                else:
                    filtered_stations.append(c)

        # Build DAG nodes:
        # Node 0: Origin (mile 0.0)
        # Nodes 1..N: Candidate fuel stations
        # Node N+1: Destination (total_distance_miles)
        N = len(filtered_stations)
        dists = [0.0] + [s['route_distance_miles'] for s in filtered_stations] + [total_distance]
        prices = [0.0] + [s['price'] for s in filtered_stations] + [0.0]

        # dp[i] holds (min_cost, parent_node_index)
        dp = [float('inf')] * (N + 2)
        parent = [-1] * (N + 2)
        dp[0] = 0.0

        # Small stopping preference penalty ($0.05) to avoid stopping at two adjacent stations 1 mile apart
        # unless there is a meaningful price discount
        STOPPING_PENALTY = 0.05

        for i in range(N + 1):
            if dp[i] == float('inf'):
                continue
            d_i = dists[i]

            for j in range(i + 1, N + 2):
                d_j = dists[j]
                gap = d_j - d_i

                if gap > self.max_range_miles:
                    # Stations are ordered by distance; no further station can be reached from i
                    break

                if gap < 0:
                    continue

                # Calculate fuel cost for the segment i -> j
                # If j is the destination (N+1), fuel for this final leg is purchased at station i (prices[i])
                # If j is a fuel station, fuel for leg i -> j is purchased at station j (prices[j])
                if j == N + 1:
                    # Reaching destination from station i
                    if i == 0:
                        # Direct from origin to destination without stopping (already checked, but safe guard)
                        leg_cost = 0.0
                    else:
                        leg_gallons = gap / self.fuel_efficiency_mpg
                        leg_cost = leg_gallons * prices[i]
                else:
                    # Reaching station j
                    leg_gallons = gap / self.fuel_efficiency_mpg
                    leg_cost = (leg_gallons * prices[j]) + STOPPING_PENALTY

                if dp[i] + leg_cost < dp[j]:
                    dp[j] = dp[i] + leg_cost
                    parent[j] = i

        # Verify reachability of destination
        if dp[N + 1] == float('inf') or parent[N + 1] == -1:
            # Analyze where the gap occurred for descriptive error message
            max_reachable_mile = 0.0
            for k in range(N + 2):
                if dp[k] != float('inf'):
                    max_reachable_mile = max(max_reachable_mile, dists[k])
            raise RouteInfeasibleException(
                f"Route infeasible: No reachable fuel stations found within the {self.max_range_miles}-mile vehicle range "
                f"beyond mile {max_reachable_mile:.1f} (destination is at mile {total_distance:.1f})."
            )

        # Reconstruct optimal path of stop indices
        path_indices = []
        curr = N + 1
        while curr != -1:
            path_indices.append(curr)
            curr = parent[curr]
        path_indices.reverse()

        # Extract selected fuel stops (excluding node 0 and node N+1)
        selected_stop_indices = [idx for idx in path_indices if idx != 0 and idx != N + 1]
        
        # Build detailed stop list with exact gallon allocations
        fuel_stops: List[Dict[str, Any]] = []
        total_calculated_cost = 0.0

        # Compute gallons purchased at each stop:
        # At stop k, the driver fuels for the segment just completed plus the final segment if it's the last stop
        for pos, stop_idx in enumerate(selected_stop_indices):
            station_info = filtered_stations[stop_idx - 1]
            prev_node_idx = path_indices[path_indices.index(stop_idx) - 1]
            prev_dist = dists[prev_node_idx]
            curr_dist = dists[stop_idx]

            leg_distance = curr_dist - prev_dist
            gallons = leg_distance / self.fuel_efficiency_mpg

            # If this is the last stop before destination, also add the fuel needed to reach the destination
            is_last_stop = (pos == len(selected_stop_indices) - 1)
            if is_last_stop:
                final_leg_dist = total_distance - curr_dist
                gallons += (final_leg_dist / self.fuel_efficiency_mpg)

            gallons_rounded = round(gallons, 2)
            unit_price = station_info['price']
            stop_cost = round(gallons_rounded * unit_price, 2)
            total_calculated_cost += stop_cost

            fuel_stops.append({
                "stop_number": pos + 1,
                "opis_id": station_info.get('opis_id', ''),
                "name": station_info['name'],
                "address": station_info.get('address', ''),
                "city": station_info['city'],
                "state": station_info['state'],
                "latitude": station_info['latitude'],
                "longitude": station_info['longitude'],
                "route_distance_miles": round(curr_dist, 1),
                "distance_from_route_miles": station_info.get('dist_to_route_miles', 0.0),
                "fuel_price_per_gallon": round(unit_price, 3),
                "gallons_purchased": gallons_rounded,
                "fuel_cost": stop_cost
            })

        total_cost_rounded = round(total_calculated_cost, 2)

        return {
            "vehicle": {
                "max_range_miles": self.max_range_miles,
                "fuel_efficiency_mpg": self.fuel_efficiency_mpg,
                "tank_capacity_gallons": round(self.max_range_miles / self.fuel_efficiency_mpg, 1)
            },
            "fuel_stops": fuel_stops,
            "fuel": {
                "total_gallons": total_gallons,
                "total_cost": total_cost_rounded
            },
            "stops_required": len(fuel_stops),
            "optimization_summary": {
                "candidate_stations_evaluated": len(candidates),
                "algorithm": "DAG Shortest Path Dynamic Programming",
                "objective": "Minimize total fuel cost within 500-mile leg constraint"
            }
        }


def get_optimization_service() -> OptimizationService:
    """Factory function for OptimizationService."""
    return OptimizationService()
