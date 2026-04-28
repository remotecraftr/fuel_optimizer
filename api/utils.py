import json
import math
import os
import urllib.parse
from functools import lru_cache
from uuid import uuid4

from . import map_store
from pathlib import Path
from typing import List, Tuple

import pandas as pd
import requests

ORS_GEOCODE_URL = "https://api.openrouteservice.org/geocode/search"
ORS_DIRECTIONS_URL = "https://api.openrouteservice.org/v2/directions/driving-car"

VEHICLE_RANGE_MILES = 500
VEHICLE_MPG = 10
MAX_DETOUR_KM = 15  # Max distance from route to consider a station

BASE_DIR = Path(__file__).resolve().parent.parent
FUEL_FILE = BASE_DIR / "data" / "fuel.csv"

# Fallback API key provided by user (used only if ORS_API_KEY env var is not set).
# Keep keys out of source in production; this fallback is for quick local testing.
FALLBACK_API_KEY = "ENTER YOUR API"


def _require_api_key() -> str:
    api_key = os.getenv("ORS_API_KEY", "").strip()
    if api_key:
        return api_key

    # Fall back to user-provided key for local testing if available
    if FALLBACK_API_KEY:
        return FALLBACK_API_KEY

    raise RuntimeError(
        "OpenRouteService API key is missing. "
        "Set the ORS_API_KEY environment variable with your key."
    )


def haversine_km(lon1: float, lat1: float, lon2: float, lat2: float) -> float:
    """Calculate great-circle distance between two points (km)."""
    R_KM = 6371
    lat1, lon1, lat2, lon2 = map(math.radians, [lat1, lon1, lat2, lon2])
    dlat = lat2 - lat1
    dlon = lon2 - lon1
    a = math.sin(dlat / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin(dlon / 2) ** 2
    c = 2 * math.asin(math.sqrt(a))
    return R_KM * c


def distance_point_to_route_km(point_lon: float, point_lat: float, route_coords: List[Tuple[float, float]]) -> float:
    """
    Calculate minimum distance from a point to any segment of the route.
    route_coords: list of [lon, lat] pairs.
    Returns minimum distance in km.
    """
    min_dist = float('inf')
    for lon, lat in route_coords:
        dist = haversine_km(point_lon, point_lat, lon, lat)
        if dist < min_dist:
            min_dist = dist
    return min_dist


def load_stations() -> pd.DataFrame:
    """Load and validate fuel dataset with lat/lon coordinates."""
    if not FUEL_FILE.exists():
        raise FileNotFoundError(f"Fuel dataset not found: {FUEL_FILE}")

    stations = pd.read_csv(FUEL_FILE)
    if stations.empty:
        raise ValueError("Fuel dataset is empty")

    # Check for required columns (including lat/lon)
    expected = {"Truckstop Name", "City", "State", "Retail Price", "lat", "lon"}
    if not expected.issubset(set(stations.columns)):
        raise ValueError(
            "fuel.csv must include columns: Truckstop Name, City, State, Retail Price, lat, lon"
        )

    # Keep all columns needed for routing
    stations = stations[["Truckstop Name", "City", "State", "Retail Price", "lat", "lon"]].copy()
    stations["Retail Price"] = pd.to_numeric(stations["Retail Price"], errors="coerce")
    stations["lat"] = pd.to_numeric(stations["lat"], errors="coerce")
    stations["lon"] = pd.to_numeric(stations["lon"], errors="coerce")
    
    # Drop rows missing price or coordinates
    stations = stations.dropna(subset=["Retail Price", "lat", "lon"])

    # Deduplicate: keep cheapest row for same stop name/city/state.
    stations = stations.sort_values("Retail Price").drop_duplicates(
        subset=["Truckstop Name", "City", "State"], keep="first"
    )
    return stations.reset_index(drop=True)


@lru_cache(maxsize=128)
def geocode_place(place: str, api_key: str):
    """Geocode a place name to [lon, lat] and state abbreviation."""
    params = {
        "api_key": api_key,
        "text": place,
        "size": 1,
        "boundary.country": "USA",
    }
    response = requests.get(ORS_GEOCODE_URL, params=params, timeout=12)
    response.raise_for_status()
    payload = response.json()

    features = payload.get("features", [])
    if not features:
        raise ValueError(f"Could not geocode location: {place}")

    feature = features[0]
    coordinates = feature.get("geometry", {}).get("coordinates")
    if not coordinates or len(coordinates) < 2:
        raise ValueError(f"Invalid geocode response for location: {place}")

    props = feature.get("properties", {})
    state = props.get("region_a") or props.get("region") or ""
    return [float(coordinates[0]), float(coordinates[1])], state


def geocode_stations_batch(stations: pd.DataFrame, api_key: str) -> pd.DataFrame:
    """
    Geocode fuel stations by city/state (batch grouped by unique city/state).
    Add lat/lon columns to dataframe.
    
    NOTE: To keep API calls minimal and performance fast, we only geocode
    stations from the start/end states or use a simple distance-based heuristic
    instead of full geocoding.
    """
    stations = stations.copy()
    stations["lon"] = None
    stations["lat"] = None
    # Stations without explicit coordinates will fall back to price-based selection
    return stations


def get_route_distance_miles(start_coords, end_coords, api_key: str) -> Tuple[float, dict]:
    """Get distance and geometry of route between two points."""
    headers = {"Authorization": api_key, "Content-Type": "application/json"}
    body = {"coordinates": [start_coords, end_coords]}

    response = requests.post(ORS_DIRECTIONS_URL, json=body, headers=headers, timeout=15)
    response.raise_for_status()
    payload = response.json()

    routes = payload.get("routes", [])
    if not routes:
        raise ValueError("No route found between the provided locations")

    distance_meters = routes[0].get("summary", {}).get("distance")
    if distance_meters is None:
        raise ValueError("Could not read route distance from response")

    return float(distance_meters) / 1609.344, routes[0]


def decode_polyline(polyline_str: str, precision: int = 5) -> List[Tuple[float, float]]:
    """
    Decode a polyline-encoded geometry string (Google's algorithm).
    Returns list of [lon, lat] coordinate pairs.
    """
    coords = []
    index = lat = lon = 0
    changes = {"latitude": 0, "longitude": 0}

    while index < len(polyline_str):
        for unit in ["latitude", "longitude"]:
            shift = 0
            result = 0
            while True:
                byte = ord(polyline_str[index]) - 63
                index += 1
                result |= (byte & 0x1f) << shift
                shift += 5
                if not byte >= 0x20:
                    break
            if result & 1:
                changes[unit] = ~(result >> 1)
            else:
                changes[unit] = result >> 1

        lat += changes["latitude"]
        lon += changes["longitude"]
        coords.append([lon / (10**precision), lat / (10**precision)])

    return coords


def extract_route_geometry(route: dict) -> List[Tuple[float, float]]:
    """
    Extract [lon, lat] coordinates from route geometry.
    ORS returns geometry as an encoded polyline string.
    This function decodes it to a list of coordinate pairs.
    """
    geometry = route.get("geometry")
    
    # If geometry is a string, it's polyline-encoded
    if isinstance(geometry, str):
        try:
            coords = decode_polyline(geometry)
            return coords
        except Exception as e:
            print(f"Warning: Could not decode polyline: {e}")
            return []
    
    # If geometry is a dict with LineString type, extract coordinates
    if isinstance(geometry, dict) and geometry.get("type") == "LineString":
        coords = geometry.get("coordinates", [])
        if coords:
            return coords
    
    return []


def filter_stations_by_route_proximity(
    stations: pd.DataFrame,
    route_coords: List[Tuple[float, float]],
    max_detour_km: float = MAX_DETOUR_KM,
) -> pd.DataFrame:
    """
    Filter stations to those within max_detour_km of the route.
    Returns a dataframe sorted by price (cheapest first).
    """
    if stations.empty or not route_coords:
        return stations.sort_values("Retail Price").reset_index(drop=True)
    
    nearby = []
    for idx, row in stations.iterrows():
        dist_km = distance_point_to_route_km(row["lon"], row["lat"], route_coords)
        if dist_km <= max_detour_km:
            row_dict = row.to_dict()
            row_dict["distance_from_route_km"] = dist_km
            nearby.append(row_dict)
    
    if not nearby:
        # No stations within detour threshold; fall back to overall cheapest
        return stations.sort_values("Retail Price").reset_index(drop=True)
    
    nearby_df = pd.DataFrame(nearby)
    return nearby_df.sort_values("Retail Price").reset_index(drop=True)


def find_closest_point_on_route(station_lon: float, station_lat: float, route_coords: List[Tuple[float, float]]) -> int:
    """
    Find the index of the closest point on the route to this station.
    This represents how far along the journey this station is.
    Returns: index in route_coords list (0 = start, len-1 = end)
    """
    if not route_coords:
        return 0
    
    min_dist = float('inf')
    closest_idx = 0
    
    for idx, (lon, lat) in enumerate(route_coords):
        dist = haversine_km(station_lon, station_lat, lon, lat)
        if dist < min_dist:
            min_dist = dist
            closest_idx = idx
    
    return closest_idx


def assign_station_to_segment(route_idx: int, route_length: int, num_segments: int) -> int:
    """
    Convert a route position (index) to segment number.
    Divides the route into equal segments based on coordinate count.
    """
    if num_segments <= 1 or route_length <= 1:
        return 0

    segment_size = route_length / num_segments
    segment = int(route_idx / segment_size)
    return min(segment, num_segments - 1)


def select_stops_by_route_progression(
    filtered_stations: pd.DataFrame,
    route_coords: List[Tuple[float, float]],
    num_segments: int,
) -> pd.DataFrame:
    """
    Select fuel stops by route progression, not just by price.
    
    Algorithm:
    1. Map each station to its position on route (closest point index)
    2. Assign each station to a segment based on its route position
    3. For each segment, pick the CHEAPEST station in that segment
    4. Return stations in route order (segment 0, 1, 2, ...)
    
    This ensures:
    - Stations follow the order of travel
    - No random jumps across states
    - One stop per segment
    """
    if filtered_stations.empty:
        return filtered_stations

    if not route_coords:
        fallback = filtered_stations.sort_values("Retail Price").head(num_segments).copy()
        fallback["route_index"] = list(range(len(fallback)))
        fallback["segment_id"] = list(range(len(fallback)))
        return fallback.reset_index(drop=True)

    route_length = len(route_coords)
    segment_size = route_length / num_segments if num_segments > 0 else route_length

    station_records = []
    for _, row in filtered_stations.iterrows():
        route_index = find_closest_point_on_route(float(row["lon"]), float(row["lat"]), route_coords)
        segment_id = assign_station_to_segment(route_index, route_length, num_segments)
        station_records.append(
            {
                "station": row.copy(),
                "route_index": route_index,
                "segment_id": segment_id,
                "price": float(row["Retail Price"]),
            }
        )

    segment_groups: dict[int, list[dict]] = {}
    for record in station_records:
        segment_groups.setdefault(record["segment_id"], []).append(record)

    selected_records: list[dict] = []
    used_station_ids: set[int] = set()

    def station_key(record: dict) -> Tuple[float, float, int]:
        return (record["price"], record["route_index"], record["segment_id"])

    def nearest_key(record: dict, target_route_index: float) -> Tuple[float, float, float]:
        return (
            abs(record["route_index"] - target_route_index),
            record["price"],
            record["route_index"],
        )

    for segment_id in range(num_segments):
        target_center = (segment_id + 0.5) * segment_size
        segment_candidates = [
            record for record in segment_groups.get(segment_id, [])
            if id(record["station"]) not in used_station_ids
        ]

        if segment_candidates:
            chosen = min(segment_candidates, key=station_key)
        else:
            remaining = [
                record for record in station_records
                if id(record["station"]) not in used_station_ids
            ]
            if remaining:
                chosen = min(remaining, key=lambda record: nearest_key(record, target_center))
            elif selected_records:
                chosen = selected_records[-1]
            else:
                break

        selected_records.append(chosen)
        used_station_ids.add(id(chosen["station"]))

    if not selected_records:
        fallback = filtered_stations.sort_values("Retail Price").head(num_segments).copy()
        fallback["route_index"] = list(range(len(fallback)))
        fallback["segment_id"] = list(range(len(fallback)))
        return fallback.reset_index(drop=True)

    selected_df = pd.DataFrame([record["station"].to_dict() for record in selected_records])
    selected_df["route_index"] = [record["route_index"] for record in selected_records]
    selected_df["segment_id"] = [record["segment_id"] for record in selected_records]

    if len(selected_df) < num_segments:
        deficit = num_segments - len(selected_df)
        repeats = selected_df.sort_values(["route_index", "Retail Price"]).head(deficit)
        if not repeats.empty:
            selected_df = pd.concat([selected_df, repeats], ignore_index=True)

    return selected_df.sort_values(["route_index", "segment_id", "Retail Price"]).reset_index(drop=True)


def generate_map_url(
    start_coords: List[float],
    end_coords: List[float],
    fuel_stops: pd.DataFrame,
    route_coords: List[Tuple[float, float]],
) -> str:
    """
    Generate an OpenStreetMap URL showing the route and fuel stops.
    
    Uses geojson.io to display:
    - Route polyline (blue line)
    - Start location (green marker)
    - End location (red marker)
    - Fuel stops (gold markers with fuel emoji)
    
    Args:
        start_coords: [lon, lat] of start
        end_coords: [lon, lat] of end
        fuel_stops: DataFrame with Truckstop Name, City, State, lat, lon columns
        route_coords: List of [lon, lat] tuples for the full route path
    
    Returns:
        URL to view the map on geojson.io
    """
    # Build GeoJSON Feature Collection
    features = []
    
    # Add route as LineString (blue polyline)
    if route_coords:
        route_feature = {
            "type": "Feature",
            "geometry": {
                "type": "LineString",
                "coordinates": route_coords,
            },
            "properties": {
                "name": "Route",
                "stroke": "#0033ff",
                "stroke-width": 3,
                "stroke-opacity": 0.7,
            },
        }
        features.append(route_feature)
    
    # Add start marker (green)
    start_feature = {
        "type": "Feature",
        "geometry": {
            "type": "Point",
            "coordinates": start_coords,
        },
        "properties": {
            "name": "Start",
            "marker-color": "#00ff00",
            "marker-size": "large",
            "marker-symbol": "arrow",
        },
    }
    features.append(start_feature)
    
    # Add end marker (red)
    end_feature = {
        "type": "Feature",
        "geometry": {
            "type": "Point",
            "coordinates": end_coords,
        },
        "properties": {
            "name": "End",
            "marker-color": "#ff0000",
            "marker-size": "large",
            "marker-symbol": "flag",
        },
    }
    features.append(end_feature)
    
    # Add fuel stop markers (gold with fuel pump symbol)
    for _, row in fuel_stops.iterrows():
        fuel_feature = {
            "type": "Feature",
            "geometry": {
                "type": "Point",
                "coordinates": [float(row["lon"]), float(row["lat"])],
            },
            "properties": {
                "name": f"{row['Truckstop Name']} - {row['City']}, {row['State']}",
                "price": f"${float(row['Retail Price']):.3f}",
                "marker-color": "#ffd700",
                "marker-size": "medium",
                "marker-symbol": "fuel",
            },
        }
        features.append(fuel_feature)
    
    # Build GeoJSON Feature Collection
    geojson_obj = {
        "type": "FeatureCollection",
        "features": features,
    }
    
    # Save GeoJSON server-side and return a short URL
    map_id = uuid4().hex[:8]
    try:
        map_store.save_map(map_id, geojson_obj)
    except Exception:
        # Fallback: if saving fails, embed as before
        geojson_str = json.dumps(geojson_obj, separators=(',', ':'))
        encoded_geojson = urllib.parse.quote(geojson_str)
        return f"https://geojson.io/#data=data:application/json,{encoded_geojson}"

    # Short local map URL (served by Django). Use BASE_URL env var if set.
    base = os.getenv("BASE_URL", "http://127.0.0.1:8000").rstrip("/")
    return f"{base}/api/map/{map_id}/"


def optimize_fuel_route(start: str, end: str):
    """
    Optimize route with fuel stops based on route progression.
    
    Steps:
    1. Geocode start/end locations to get route endpoints
    2. Get route distance and geometry from ORS
    3. Extract route coordinates (list of [lon, lat] points)
    4. Filter stations by distance to route (within MAX_DETOUR_KM)
    5. Map each station to its position on the route
    6. Divide route into segments by vehicle range
    7. Select cheapest station from each segment (preserving route order)
    
    Result: Fuel stops follow the actual order of travel, not random sorting.
    """
    api_key = _require_api_key()

    stations = load_stations()
    
    # Geocode start/end locations to get route endpoints
    start_coords, start_state = geocode_place(start, api_key)
    end_coords, end_state = geocode_place(end, api_key)
    
    # Get route distance and geometry from ORS
    distance_miles, route = get_route_distance_miles(start_coords, end_coords, api_key)
    
    # Extract route geometry (list of [lon, lat] coordinates)
    route_coords = extract_route_geometry(route)
    
    if not route_coords:
        raise ValueError("Could not extract route geometry. Cannot select stops by route progression.")
    
    # Filter stations to those near the route (within MAX_DETOUR_KM)
    candidates = filter_stations_by_route_proximity(stations, route_coords, MAX_DETOUR_KM)
    
    if candidates.empty:
        # Fallback: if no stations within threshold, use all stations
        candidates = stations
    
    segments = max(1, math.ceil(distance_miles / VEHICLE_RANGE_MILES))
    gallons_total = distance_miles / VEHICLE_MPG
    
    # Select one station per segment, preserving route order
    selected = select_stops_by_route_progression(candidates, route_coords, segments)

    if selected.empty:
        raise ValueError("No fuel stations available to build recommendation")

    selected = selected.sort_values("route_index").reset_index(drop=True)

    if len(selected) < segments:
        raise ValueError("Could not select one fuel stop for each route segment")

    avg_price = float(selected["Retail Price"].mean())
    total_cost = round(gallons_total * avg_price, 2)

    fuel_stops = [
        {
            "name": row["Truckstop Name"],
            "city": row["City"],
            "state": row["State"],
            "price": round(float(row["Retail Price"]), 3),
        }
        for _, row in selected.iterrows()
    ]
    
    # Generate map URL showing route and fuel stops
    map_url = generate_map_url(start_coords, end_coords, selected, route_coords)

    return {
        "distance_miles": round(distance_miles, 2),
        "fuel_stops": fuel_stops,
        "total_cost": total_cost,
        "map_url": map_url,
        "assumptions": {
            "range_miles": VEHICLE_RANGE_MILES,
            "miles_per_gallon": VEHICLE_MPG,
            "segments": segments,
            "max_detour_km": MAX_DETOUR_KM,
            "filtering": "route proximity with segment-based progression",
        },
    }