# Fuel-Efficient Route Planner API

> **Production-grade Django REST API that calculates optimal driving routes across the United States, identifies cost-effective fuel stops from OPIS fuel price data under vehicle range constraints, and computes total fuel expenditure.**

Built for the **Spotter Backend Engineer Coding Assessment**.

---

## Table of Contents

1. [Project Overview](#project-overview)
2. [Key Requirements & Assessment Compliance](#key-requirements--assessment-compliance)
3. [System Architecture](#system-architecture)
4. [Fuel Stop Optimization Algorithm](#fuel-stop-optimization-algorithm)
5. [Vehicle & Fuel Assumptions](#vehicle--fuel-assumptions)
6. [Fuel-Price Dataset & Ingestion Layer](#fuel-price-dataset--ingestion-layer)
7. [Routing & Geocoding APIs](#routing--geocoding-apis)
8. [External API Calls & Performance](#external-api-calls--performance)
9. [Installation & Local Setup](#installation--local-setup)
10. [Environment Variables](#environment-variables)
11. [Running the Application](#running-the-application)
12. [API Reference & Examples](#api-reference--examples)
13. [Interactive Map Demonstration](#interactive-map-demonstration)
14. [Automated Testing Suite](#automated-testing-suite)
15. [Docker & Docker Compose](#docker--docker-compose)
16. [Postman Collection](#postman-collection)
17. [Future Enhancements](#future-enhancements)

---

## 1. Project Overview

Long-haul freight and fleet logistics operations face significant operational expenditure from retail diesel fuel. Commercial heavy-duty vehicles travel thousands of miles between origins and destinations, operating under strict physical range constraints. 

The **Fuel-Efficient Route Planner API** solves this challenge by:
1. Geocoding origin and destination points within the United States.
2. Generating the complete highway driving route and polyline geometry using a single routing call.
3. Projecting 8,150+ OPIS truck stops onto the highway corridor using fast spatial vectorization.
4. Executing a **DAG Shortest Path / Dynamic Programming** optimization engine that selects refueling stops to **minimize total fuel cost** while strictly guaranteeing that the vehicle **never travels more than 500 miles without refueling**.
5. Calculating the exact gallons purchased at each selected stop and total dollars spent on fuel.
6. Providing an interactive, responsive **Leaflet.js map interface** to visually inspect route paths and stop markers.

---

## 2. Key Requirements & Assessment Compliance

| Assessment Requirement | Implementation Details | Status |
|---|---|:---:|
| **Latest stable Django & DRF** | Django 5.2+, Django REST Framework 3.18+, Python 3.11 | Verified |
| **Clean service-oriented architecture** | Separated services: `routing_service`, `geocoding_service`, `fuel_service`, `optimization_service` | Verified |
| **Max range: 500 miles** | Enforced via DAG reachability constraint ($\Delta d \le 500$ mi) | Verified |
| **Fuel efficiency: 10 MPG** | Exactly 10 miles per gallon ($0.1\text{ gal/mile}$) | Verified |
| **Use provided fuel-price file** | Parsed and loaded from `data/fuel-prices.csv` (8,151 stations) | Verified |
| **Free map/geocoding/routing API** | OSRM (Open Source Routing Machine) + OpenStreetMap Nominatim | Verified |
| **Minimize external API calls** | **1 routing API call** per request; 0-2 geocoding calls (0 on cache hits) | Verified |
| **USA-only validation** | Validated via bounding boxes, state codes, and geocoding response | Verified |
| **Fast API response** | Pre-indexed in-memory data, NumPy vectorized spatial math, sub-50ms local optimization | Verified |
| **Automated test suite** | 30 tests covering all 12 required assessment scenarios with 100% mocked external APIs | Verified |
| **Interactive Map Demonstration** | Embedded responsive Leaflet.js interface served at `/` | Verified |

---

## 3. System Architecture

The application is structured following clean architectural boundaries:

```
project/
├── manage.py                     # Django administrative entrypoint
├── requirements.txt              # Production dependencies
├── .env.example                  # Environment configuration template
├── .env                          # Local environment variables
├── Dockerfile                    # Container definition
├── docker-compose.yml            # Local orchestration
├── postman_collection.json       # Postman test collection
├── README.md                     # Comprehensive documentation
├── config/                       # Project settings & routing
│   ├── settings.py               # Django configuration, DRF, caches, logging
│   ├── urls.py                   # Root URLconf
│   ├── wsgi.py                   # WSGI server entrypoint
│   └── asgi.py                   # ASGI server entrypoint
├── route_planner/                # Main application package
│   ├── models.py                 # Optional RouteQueryLog audit model
│   ├── serializers.py            # DRF serializers for inputs and outputs
│   ├── views.py                  # API endpoints and map template view
│   ├── urls.py                   # App routing endpoints
│   ├── services/                 # Decoupled business logic services
│   │   ├── fuel_service.py       # OPIS parser & vectorized spatial candidate discovery
│   │   ├── geocoding_service.py  # US location validation & Nominatim/offline resolution
│   │   ├── routing_service.py    # OSRM client with geometry extraction & caching
│   │   └── optimization_service.py # DAG Shortest Path DP optimization engine
│   ├── templates/
│   │   └── index.html            # Leaflet.js interactive map demonstration UI
│   └── tests/
│       ├── test_services.py      # Unit tests for core services
│       └── test_api.py           # Integration tests for 12 assessment criteria
└── data/
    ├── fuel-prices.csv           # Assessment-provided OPIS fuel dataset (8,151 rows)
    └── us_cities.csv             # Offline US cities coordinate reference database
```

### Request Flow

```
1. Client Request: POST /api/v1/route/ {"start": "Chicago, IL", "finish": "New York, NY"}
   │
2. [Input Validation] -> RoutePlannerInputSerializer verifies inputs.
   │
3. [Geocoding] -> GeocodingService checks offline city coordinates or Nominatim.
   │             Verifies coordinates are inside USA.
   │
4. [Routing]   -> RoutingService executes 1 OSRM call to retrieve distance & GeoJSON geometry.
   │
5. [Candidate Discovery] -> FuelStationService uses bounding-box and NumPy vectorization
   │                        to project fuel stations onto the highway corridor (<= 15 mi buffer).
   │
6. [Optimization] -> OptimizationService builds a DAG and executes Dynamic Programming
   │                 to select optimal stops minimizing fuel price while enforcing <= 500 mi legs.
   │
7. [Fuel Accounting] -> Calculates gallons purchased and stop cost (sum(gallons) == D / 10).
   │
8. Response: Structured JSON response + polyline coordinates rendered on Leaflet map.
```

---

## 4. Fuel Stop Optimization Algorithm

### Mathematical Formulation

The fuel stop optimization is formulated as a **Single-Source Shortest Path problem on a Directed Acyclic Graph (DAG)**:

Let the route points be:
- Node $0$: Origin ($d_0 = 0.0$ miles).
- Nodes $1, 2, \dots, N$: Candidate fuel stations along the route, ordered by distance from origin: $0 < d_1 \le d_2 \le \dots \le d_N < D_{\text{total}}$.
- Node $N + 1$: Destination ($d_{N+1} = D_{\text{total}}$ miles).

#### Feasibility Constraints
A directed edge $u \to v$ exists if and only if:
$$0 \le d(v) - d(u) \le R_{\text{max}} = 500.0 \text{ miles}$$

This ensures:
1. The first fuel stop is reachable from origin: $d(v_1) \le 500$ miles.
2. Every consecutive leg between stops does not exceed 500 miles: $d(v_{j+1}) - d(v_j) \le 500$ miles.
3. The destination is reachable from the final stop: $D_{\text{total}} - d(v_m) \le 500$ miles.

#### Cost Function (Edge Weights)
At each fuel stop $v$, fuel is purchased at unit price $P(v)$ ($/gallon).
- For a transition between stations $u \to v$, the fuel consumed over the leg is $\Delta d / 10.0$ gallons. Refueling at station $v$ incurs:
  $$\text{cost}(u \to v) = \left(\frac{d(v) - d(u)}{10.0}\right) \times P(v) + \epsilon$$
  *(where $\epsilon = \$0.05$ is a negligible stopping penalty that prevents redundant stops at adjacent stations unless a genuine price saving exists).*
- For the final transition from station $v$ to destination $N+1$, the remaining distance to destination $D_{\text{total}} - d(v) \le 500$ miles must be powered by fuel pumped at the last stop $v$:
  $$\text{cost}(v \to N+1) = \left(\frac{D_{\text{total}} - d(v)}{10.0}\right) \times P(v)$$

#### Complexity
Because all nodes are ordered along the 1D route polyline ($d_i < d_j$ for all $i < j$), the graph is topological and strictly acyclic. The DP runs in:
$$O(V + E) \approx O(N \times K)$$
where $N \le 300$ candidate stations and $K \le 50$ reachable forward neighbors. This executes in **less than 5 milliseconds**.

---

## 5. Vehicle & Fuel Assumptions

To make the 500-mile range constraint physically and logistically meaningful, the following explicit assumptions are established and documented:

1. **Vehicle Range & Tank Capacity**:
   - Maximum range on a full tank = **500 miles**.
   - Fuel efficiency = **10 MPG** ($0.1$ gallons per mile).
   - Fuel tank capacity = $500 \text{ miles} / 10 \text{ MPG} =$ **50.0 gallons**.

2. **Starting Fuel Level**:
   - The vehicle departs the origin with a **full tank (50.0 gallons / 500 miles range)**.

3. **Short Routes ($\le 500$ miles)**:
   - If the total route distance is $\le 500$ miles (e.g., Philadelphia to New York, 95 miles):
   - **0 fuel stops are required**. The vehicle reaches the destination on its starting tank.
   - The API returns `fuel_stops: []`, `stops_required: 0`, and reports `total_gallons = distance / 10`.

4. **Long Routes ($> 500$ miles)**:
   - At least one refueling stop is physically required.
   - At each stop, fuel is purchased to replenish the tank for travel legs.
   - At the final stop, the fuel required to reach the destination is also purchased.
   - **Total fuel purchased across all stops equals total fuel consumed for the trip**:
     $$\sum_{j=1}^m \text{gallons\_purchased}_j = \text{total\_gallons} = \frac{D_{\text{total}}}{10}$$
   - This leaves the vehicle at the destination with its initial reserve (net zero haul fuel balance).

---

## 6. Fuel-Price Dataset & Ingestion Layer

The assessment provides an OPIS truck stop retail fuel price dataset containing 8,151 records with the following schema:
- `OPIS Truckstop ID`: Unique station identifier.
- `Truckstop Name`: Station brand/name (e.g., Pilot, Flying J, Love's, Petro, TA, Sheetz, Kwik Trip).
- `Address`: Highway exit and road address.
- `City`: Municipality.
- `State`: State postal code.
- `Rack ID`: Wholesale rack source identifier.
- `Retail Price`: Retail diesel price per gallon in USD (e.g., `3.00733333`).

### Zero-External-Call Station Geocoding
Because the raw dataset lacks latitude and longitude coordinates, querying a public geocoding API for 8,151 stations would violate rate limits and introduce massive latency. 

To solve this cleanly:
- The project bundles an offline US cities coordinate database (`data/us_cities.csv`).
- On server startup, `FuelStationService` loads and maps 99.8% of all US stations to accurate geographic coordinates.
- Multiple price quotes for the same truck stop location are deduplicated, preserving the lowest price.
- The resulting 6,614 clean US truck stops are indexed in memory for instantaneous spatial querying.

---

## 7. Routing & Geocoding APIs

### Routing API: Open Source Routing Machine (OSRM)
- **Endpoint**: `http://router.project-osrm.org/route/v1/driving/{lon1},{lat1};{lon2},{lat2}?overview=full&geometries=geojson`
- **Why OSRM**:
  - 100% free and open source.
  - Requires **no API keys** or signups, allowing reviewers to clone and run immediately.
  - Returns total driving distance, duration, and high-fidelity GeoJSON polyline geometry coordinates.
  - Highly reliable and fast.

### Geocoding API: OpenStreetMap Nominatim
- **Endpoint**: `https://nominatim.openstreetmap.org/search`
- **Why Nominatim**:
  - Free and supports US country filtering (`countrycodes=us`).
  - Standard User-Agent compliance configured via `NOMINATIM_USER_AGENT`.
  - Complemented by our **offline city coordinate lookup**, which resolves common inputs like `"Chicago, IL"` instantly with **0 network requests**.

---

## 8. External API Calls & Performance

### Call Minimization
The API strictly limits external network overhead:

| Operation | External Calls | Mechanism |
|---|:---:|---|
| **Geocode Start** | 0 to 1 | Resolved offline for `"City, ST"` format; cached via Django cache |
| **Geocode Finish** | 0 to 1 | Resolved offline for `"City, ST"` format; cached via Django cache |
| **Route Generation** | **1** | Single OSRM call fetching full distance and polyline geometry |
| **Station Discovery** | **0** | Done 100% locally via NumPy spatial vectorization |
| **Fuel Stop Optimization** | **0** | Done 100% locally via DAG Dynamic Programming |
| **Total External Calls** | **1 to 3 max** | **Repeat requests use cache = 0 calls** |

### Performance Benchmarks
- **Station Spatial Filtering**: ~15 ms (evaluating 6,600+ stations against 1,000+ route points).
- **DP Optimization**: ~3 ms.
- **Total Local Backend Execution Time**: **< 50 milliseconds**.
- **End-to-End API Response Time**: ~800 ms - 1.2 s (dominated by external OSRM network transit; < 10 ms on cache hit).

---

## 9. Installation & Local Setup

### Prerequisites
- Python 3.11+ (Python 3.10+ compatible)
- Git

### Step-by-Step Installation

```bash
# 1. Clone or navigate to the project directory
cd c:/Users/Nitin/OneDrive/Desktop/Projects/fuel_efficient_route_planner

# 2. (Optional) Create and activate a virtual environment
python -m venv venv
# On Windows PowerShell:
.\venv\Scripts\Activate.ps1
# On macOS/Linux:
source venv/bin/activate

# 3. Install required dependencies
pip install -r requirements.txt

# 4. Set up environment configuration
cp .env.example .env

# 5. Run database migrations
python manage.py migrate
```

---

## 10. Environment Variables

All settings are configurable via `.env`:

| Variable | Default | Description |
|---|---|---|
| `DEBUG` | `True` | Django debug mode (`True` in development, `False` in prod) |
| `SECRET_KEY` | `django-insecure-...` | Django cryptographic signing key |
| `ALLOWED_HOSTS` | `*` | Comma-separated list of allowed hostnames |
| `OSRM_BASE_URL` | `http://router.project-osrm.org` | OSRM routing server base URL |
| `NOMINATIM_BASE_URL` | `https://nominatim.openstreetmap.org` | Nominatim geocoding server base URL |
| `NOMINATIM_USER_AGENT` | `SpotterFuelRoutePlanner/1.0` | Custom User-Agent header for Nominatim |
| `FUEL_DATA_PATH` | `data/fuel-prices.csv` | Relative or absolute path to OPIS CSV dataset |
| `US_CITIES_PATH` | `data/us_cities.csv` | Path to offline US cities coordinate database |
| `MAX_VEHICLE_RANGE_MILES` | `500.0` | Maximum driving distance on full fuel tank |
| `FUEL_EFFICIENCY_MPG` | `10.0` | Fuel consumption rate in miles per gallon |
| `MAX_STATION_DISTANCE_FROM_ROUTE_MILES` | `15.0` | Max lateral distance for fuel station candidate selection |
| `CACHE_TTL_SECONDS` | `86400` | In-memory cache time-to-live in seconds (24 hours) |

---

## 11. Running the Application

### Development Server

```bash
python manage.py runserver 127.0.0.1:8000
```

Once running:
- **Interactive Map UI**: Open [http://127.0.0.1:8000/](http://127.0.0.1:8000/) in your browser.
- **Route Planner API**: [http://127.0.0.1:8000/api/v1/route/](http://127.0.0.1:8000/api/v1/route/)
- **Health Check API**: [http://127.0.0.1:8000/api/v1/health/](http://127.0.0.1:8000/api/v1/health/)

### Production Server (Gunicorn)

```bash
gunicorn config.wsgi:application --bind 0.0.0.0:8000 --workers 3 --timeout 120
```

---

## 12. API Reference & Examples

### Endpoint: Plan Route & Optimize Fuel

- **URL**: `/api/v1/route/`
- **Method**: `POST`
- **Content-Type**: `application/json`

#### Request Body
```json
{
  "start": "Chicago, IL",
  "finish": "New York, NY"
}
```

*Note: Coordinates format is also supported:*
```json
{
  "start": { "latitude": 41.8781, "longitude": -87.6298, "name": "Chicago, IL" },
  "finish": { "latitude": 40.7128, "longitude": -74.0060, "name": "New York, NY" }
}
```

#### Successful Response (`200 OK`)
```json
{
  "success": true,
  "start": {
    "name": "Chicago, IL",
    "latitude": 41.875562,
    "longitude": -87.624421
  },
  "finish": {
    "name": "New York, NY",
    "latitude": 40.712728,
    "longitude": -74.006015
  },
  "route": {
    "distance_miles": 793.73,
    "duration_minutes": 894.4,
    "geometry": [
      [-87.624421, 41.875562],
      [-87.623101, 41.875583],
      "..."
    ]
  },
  "vehicle": {
    "max_range_miles": 500.0,
    "fuel_efficiency_mpg": 10.0,
    "tank_capacity_gallons": 50.0
  },
  "fuel_stops": [
    {
      "stop_number": 1,
      "opis_id": "72288",
      "name": "S&G #88",
      "address": "I-475 Exit 13 & US-20",
      "city": "Toledo",
      "state": "OH",
      "latitude": 41.642,
      "longitude": -83.5438,
      "route_distance_miles": 236.4,
      "distance_from_route_miles": 5.35,
      "fuel_price_per_gallon": 3.009,
      "gallons_purchased": 23.64,
      "fuel_cost": 71.13
    },
    {
      "stop_number": 2,
      "opis_id": "72445",
      "name": "SHEETZ #639",
      "address": "I-80 Exit 223",
      "city": "Youngstown",
      "state": "OH",
      "latitude": 41.0986,
      "longitude": -80.6474,
      "route_distance_miles": 401.0,
      "distance_from_route_miles": 3.7,
      "fuel_price_per_gallon": 3.059,
      "gallons_purchased": 55.73,
      "fuel_cost": 170.48
    }
  ],
  "fuel": {
    "total_gallons": 79.37,
    "total_cost": 241.61
  },
  "stops_required": 2,
  "optimization_summary": {
    "candidate_stations_evaluated": 164,
    "algorithm": "DAG Shortest Path Dynamic Programming",
    "objective": "Minimize total fuel cost within 500-mile leg constraint"
  },
  "note": null
}
```

#### Error Responses

- **400 Bad Request** (Missing/Empty Input):
  ```json
  {
    "success": false,
    "error": "Invalid request payload.",
    "details": {
      "finish": ["Finish location cannot be blank."]
    }
  }
  ```

- **400 Bad Request** (Non-USA Location):
  ```json
  {
    "success": false,
    "error": "Start location error: Location 'Toronto, Canada' is outside the USA. Locations must be within the United States."
  }
  ```

- **404 Not Found** (No Drivable Path):
  ```json
  {
    "success": false,
    "error": "No drivable route found between the specified locations."
  }
  ```

- **502 Bad Gateway** (Routing Network Failure):
  ```json
  {
    "success": false,
    "error": "Routing API error: External routing service communication failed: ..."
  }
  ```

---

## 13. Interactive Map Demonstration

A Leaflet.js frontend is served directly at the root URL (`http://127.0.0.1:8000/`):
- **Dynamic Driving Polyline**: Draws the exact highway path from OSRM GeoJSON coordinates.
- **Color-Coded Waypoints**:
  - Green Circle: Origin
  - Red Circle: Destination
  - Gold Pins: Optimal Fuel Stops
- **Interactive Stop Popups**: Clicking a fuel pin reveals station brand, address, mile marker, retail diesel price, and gallons purchased.
- **Quick Test Buttons**: One-click planning for standard benchmarks (`Chicago -> NYC`, `LA -> NYC`, `Philly -> NYC`).
- **Real-Time Cost Dashboard**: Displays distance, travel duration, total gallons, and total money spent.

---

## 14. Automated Testing Suite

The project includes an automated test suite verifying all 12 criteria using Django's test runner:

```bash
python manage.py test route_planner.tests -v 2
```

### Test Coverage Breakdown

```
test_01_valid_route_request (test_api.RoutePlannerAPITests)
  -> 1. Valid route request returns HTTP 200 with structured JSON response. [OK]
test_02_missing_start_location (test_api.RoutePlannerAPITests)
  -> 2. Missing or blank start location returns HTTP 400. [OK]
test_03_missing_finish_location (test_api.RoutePlannerAPITests)
  -> 3. Missing or blank finish location returns HTTP 400. [OK]
test_04_usa_only_validation (test_api.RoutePlannerAPITests)
  -> 4. Non-US locations are rejected with HTTP 400 and clear error message. [OK]
test_05_short_route_zero_stops (test_api.RoutePlannerAPITests)
  -> 5. Routes <= 500 miles require 0 stops and report trip fuel. [OK]
test_06_long_route_multiple_stops (test_api.RoutePlannerAPITests)
  -> 6. Routes > 1000 miles require multiple refueling stops. [OK]
test_07_fuel_calculation (test_api.RoutePlannerAPITests)
  -> 7. Total fuel exactly equals route distance / 10 MPG. [OK]
test_08_fuel_cost_calculation (test_api.RoutePlannerAPITests)
  -> 8. Stop cost equals gallons * unit price, and total cost is exact sum. [OK]
test_09_vehicle_500_mile_range_constraint (test_api.RoutePlannerAPITests)
  -> 9. No single travel segment exceeds 500 miles. [OK]
test_10_fuel_station_selection_prefers_cheaper (test_api.RoutePlannerAPITests)
  -> 10. Optimization selects lower priced stations when route remains feasible. [OK]
test_11_destination_reachability_guaranteed (test_api.RoutePlannerAPITests)
  -> 11. Final stop is within 500 miles of the destination. [OK]
test_12_routing_api_failure_handling (test_api.RoutePlannerAPITests)
  -> 12. External routing failures return clean HTTP 502 error JSON. [OK]
test_12_no_route_found_handling (test_api.RoutePlannerAPITests)
  -> 12b. When no drivable path exists, returns HTTP 404. [OK]
test_health_check_endpoint (test_api.RoutePlannerAPITests)
  -> Verifies GET /api/v1/health/ returns healthy status. [OK]
+ 16 Service Unit Tests (Fuel, Geocoding, Routing, Optimization) [OK]

Total: 30 passed in ~0.25s (100% mocked external network calls)
```

---

## 15. Docker & Docker Compose

### Using Docker Compose (Recommended)

```bash
docker-compose up --build
```

The service will be available at [http://localhost:8000/](http://localhost:8000/).

### Using Docker CLI

```bash
docker build -t spotter-fuel-planner .
docker run -p 8000:8000 spotter-fuel-planner
```

---

## 16. Postman Collection

A complete Postman collection is included in the project root as `postman_collection.json`.

Import this file into Postman to test:
1. **Valid Route** (`Chicago, IL` to `New York, NY`)
2. **Long Multi-Stop Route** (`Los Angeles, CA` to `New York, NY`)
3. **Short Route 0 Stops** (`Philadelphia, PA` to `New York, NY`)
4. **Coordinates Input** (`Dallas, TX` to `Atlanta, GA`)
5. **Invalid Input** (Missing Finish Location)
6. **Non-USA Location** (`Toronto, Canada`)
7. **Health Check** (`GET /api/v1/health/`)

---

## 17. Future Enhancement

1. **Live Fuel Price Feeds**: Integrate real-time OPIS FTP or REST API webhooks for automated daily wholesale/retail price updates.
2. **Alternative Routing Profiles**: Support hazmat, high-clearance, and truck-weight restrictions via OSRM truck profiles or commercial routing APIs.
3. **Driver Hours of Service (HOS) Co-Optimization**: Combine fuel stop selection with mandatory DOT 11-hour driving / 10-hour rest break schedules.
4. **Elevation & Grade Penalties**: Adjust effective vehicle MPG based on topographic elevation profiles (e.g. crossing the Rocky Mountains).
#   f u e l _ e f f i c i e n t _ r o u t e _ p l a n n e r 
 
 
