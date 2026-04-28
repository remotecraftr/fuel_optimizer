from rest_framework import status
from rest_framework.response import Response
from rest_framework.views import APIView

from .utils import optimize_fuel_route
from .map_store import get_map
from django.http import HttpResponse, JsonResponse
from django.views import View
import json


class ApiIndexView(APIView):
    def get(self, request):
        return Response(
            {
                "name": "Fuel Optimizer API",
                "endpoints": {
                    "health": "/api/health/",
                    "route": "/api/route/",
                },
                "sample_request": {
                    "start": "Dallas, TX",
                    "end": "Austin, TX",
                },
            }
        )


class HealthView(APIView):
    def get(self, request):
        return Response({"status": "ok"})


class RouteView(APIView):
    def get(self, request):
        return Response(
            {
                "info": "Send POST JSON to calculate optimized fuel stops",
                "request_body": {
                    "start": "Dallas, TX",
                    "end": "Austin, TX",
                },
            }
        )

    def post(self, request):
        start = str(request.data.get("start", "")).strip()
        end = str(request.data.get("end", "")).strip()

        if not start or not end:
            return Response(
                {"error": "Both 'start' and 'end' are required."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        try:
            result = optimize_fuel_route(start, end)
            return Response(result, status=status.HTTP_200_OK)
        except ValueError as exc:
            return Response({"error": str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        except RuntimeError as exc:
            return Response({"error": str(exc)}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)
        except Exception as e:
            return Response({"error": f"Unexpected server error: {e}"}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

class MapDataView(APIView):
        def get(self, request, map_id: str):
                geojson = get_map(map_id)
                if not geojson:
                        return Response({"error": "Map not found"}, status=status.HTTP_404_NOT_FOUND)
                return JsonResponse(geojson, safe=False)


class MapView(View):
        def get(self, request, map_id: str):
                geojson = get_map(map_id)
                if not geojson:
                        return HttpResponse("<h3>Map not found</h3>", status=404)

                # Serve a simple Leaflet page that fetches the geojson data
                html = """
<!doctype html>
<html>
    <head>
        <meta charset="utf-8" />
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
        <title>Route Map</title>
        <link rel="stylesheet" href="https://unpkg.com/leaflet@1.9.4/dist/leaflet.css" />
        <style>html,body,#map{height:100%;margin:0;padding:0}#map{width:100%;height:100vh}</style>
    </head>
    <body>
        <div id="map"></div>
        <script src="https://unpkg.com/leaflet@1.9.4/dist/leaflet.js"></script>
        <script>
            const map = L.map('map').setView([37.8, -96], 4);
            L.tileLayer('https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png', {
                maxZoom: 19,
                attribution: '&copy; OpenStreetMap contributors'
            }).addTo(map);

            fetch('/api/map/{map_id}/data/')
                .then(r => { if(!r.ok) throw new Error('Not found'); return r.json() })
                .then(geojson => {
                    const gj = L.geoJSON(geojson, {
                        style: function(feature) {
                            return feature.properties && feature.properties.stroke ? {color: feature.properties.stroke, weight: feature.properties['stroke-width'] || 3} : {};
                        },
                        pointToLayer: function(feature, latlng) {
                            const props = feature.properties || {};

                            // Start / End keep marker icons
                            if (props && props.name === 'Start') {
                                return L.marker(latlng, {icon: L.icon({iconUrl: 'https://cdn.jsdelivr.net/gh/pointhi/leaflet-color-markers@master/img/marker-icon-green.png', iconSize:[25,41], iconAnchor:[12,41]})});
                            } else if (props && props.name === 'End') {
                                return L.marker(latlng, {icon: L.icon({iconUrl: 'https://cdn.jsdelivr.net/gh/pointhi/leaflet-color-markers@master/img/marker-icon-red.png', iconSize:[25,41], iconAnchor:[12,41]})});
                            }

                            // Fuel stops: check multiple possible property names and render as emoji
                            const markerSymbol = (props['marker-symbol'] || props.marker_symbol || '').toString().toLowerCase();
                            const looksLikeFuel = markerSymbol.includes('fuel') || (props.price !== undefined && props.price !== null);
                            if (looksLikeFuel) {
                                return L.marker(latlng, {icon: L.divIcon({className: 'fuel-marker', html: '<div style="font-size:24px;line-height:24px">⛽</div>', iconSize: [24,24], iconAnchor: [12,12]})});
                            }

                            return L.marker(latlng);
                        }
                    }).addTo(map);
                    map.fitBounds(gj.getBounds(), {padding:[20,20]});
                })
                .catch(err => {
                    document.body.innerHTML = '<h3>Could not load map</h3><pre>'+err+'</pre>'
                });
        </script>
    </body>
</html>
"""

                # Insert the map_id into the HTML without using f-string braces
                html = html.replace("{map_id}", map_id)

                return HttpResponse(html)