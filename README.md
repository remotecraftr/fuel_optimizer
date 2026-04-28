# Fuel Optimizer API

A Django REST API that calculates route distance and recommends fuel stops using fuel price optimization logic.

## Features

- `POST /api/route/` for route + fuel optimization with short interactive map link
- `GET /api/health/` health check
- `GET /api/` API index with endpoint docs
- Uses OpenRouteService for geocoding + route distance
- Cleans duplicate fuel entries and picks cheapest stops
- **Generates a short map link served by the API** (Leaflet + OpenStreetMap)
- JSON-only API responses

## Project Structure

```text
fuel_optimizer_main/
├── manage.py
├── requirements.txt
├── README.md
├── fuel_optimizer/
│   ├── __init__.py
│   ├── settings.py
│   ├── urls.py
│   ├── asgi.py
   │   └── wsgi.py
├── api/
│   ├── __init__.py
│   ├── urls.py
│   ├── views.py
│   ├── utils.py
│   └── map_store.py
└── data/
    └── fuel.csv
```

## Requirements

- Python 3.10+
- OpenRouteService API key (free tier)

Create a free key here:
- https://openrouteservice.org/dev/#/signup/

Set the key in your shell:

### PowerShell

```powershell
$env:ORS_API_KEY = "enter your api here"
```

## Install and Run

```powershell
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
python manage.py migrate
python manage.py runserver
```

Server:
- http://127.0.0.1:8000/

## Endpoints

### 1) Health

`GET /api/health/`

Example response:

```json
{
  "status": "ok"
}
```

### 2) Route Optimization

`POST /api/route/`

Request body:

```json
{
  "start": "Dallas, TX",
  "end": "Austin, TX"
}
```

Example response (note short `map_url`):

```json
{
  "distance_miles": 1000.42,
  "fuel_stops": [
    {
      "name": "THE EFFINGHAM CHROME SHOP",
      "city": "Effingham",
      "state": "IL",
      "price": 3.349
    },
    {
      "name": "Brew Stuart Truckstop",
      "city": "Stuart",
      "state": "IA",
      "price": 3.099
    },
    {
      "name": "CUBBYS #2101",
      "city": "Gothenburg",
      "state": "NE",
      "price": 3.132
    }
  ],
  "total_cost": 319.48,
  "map_url": "http://127.0.0.1:8000/api/map/ab12cd34/",
  "assumptions": {
    "range_miles": 500,
    "miles_per_gallon": 10,
    "segments": 3,
    "max_detour_km": 15,
    "filtering": "route proximity with segment-based progression"
  }
}
```

The `map_url` field now contains a short API link that opens an interactive map built with Leaflet and OpenStreetMap tiles. The map fetches stored GeoJSON from the server rather than embedding it in the URL.

### Map Viewer Endpoints

- `GET /api/map/<map_id>/` — serves a simple HTML page with a Leaflet map
- `GET /api/map/<map_id>/data/` — returns the stored GeoJSON FeatureCollection

Markers & visuals:
- Blue line: full route polyline
- Green marker: start location
- Red marker: end location
- Petrol emoji (⛽): fuel stops

Notes:
- Maps are stored in-memory by `api/map_store.py` with a default TTL (24h). This is lightweight and avoids huge URLs. For production you can replace `map_store.py` with file or DB storage.
- Use the `BASE_URL` environment variable to control the base returned in `map_url` (defaults to `http://127.0.0.1:8000`).

<img width="2879" height="1489" alt="image" src="https://github.com/user-attachments/assets/618a5569-a22a-4768-b29b-db193bab5b7d" />


## Postman Testing

1. Method: `POST`
2. URL: `http://127.0.0.1:8000/api/route/`
3. Body: `raw` + `JSON`
4. Payload:

```json
{
  "start": "Dallas, TX",
  "end": "Austin, TX"
}
```

Open the returned `map_url` in a browser to view the interactive route.

## Fuel Logic (Segment-Based Route Progression)

The system selects fuel stops that **follow the actual order of travel**, ensuring realistic geographic progression.

### Algorithm

1. **Load & Filter Stations**
   - Load ~8,000 fuel stations with lat/lon coordinates
   - Filter to only those within 15km of the actual route path

2. **Map Stations to Route**
   - For each station, find its closest point on the route (Haversine distance)
   - Store the index position along the route (0 = start, N = end)

3. **Divide Route into Segments**
   - Vehicle range = 500 miles
   - Segments = ceil(route_distance / 500)
   - Route coordinates divided proportionally across segments

4. **Segment-Based Station Selection**
   - Assign each station to its segment based on route position
   - **For each segment: pick only the CHEAPEST station in that segment**
   - Result: exactly 1 stop per segment, in strict route order

5. **Cost Calculation**
   - Total gallons = route_distance / 10 MPG
   - Total cost = gallons × average price of selected stops

### Key Improvements

✅ **Route progression guaranteed** — Stops follow travel order (no backward jumps)  
✅ **Realistic geography** — Multi-state routes respect actual path progression  
✅ **Segment-aware** — Each 500-mile segment gets exactly one fuel stop  
✅ **Cost-optimized** — Cheapest station selected within each segment  
✅ **No random sorting** — Selection based on position, not just price

## Video Walkthrough

I recorded a short walkthrough that explains the implementation and demonstrates the map viewer:

https://www.loom.com/share/1761bb61d55d443ab494d26daaebcab0

## Error Handling

- Missing `start`/`end`: 400
- Invalid location: 400
- Missing ORS API key: 500 with setup hint
- External API failure: 500 with readable message
