# app.py
import os
import tempfile
import random
import requests
from flask import Flask, request, send_file, render_template_string, abort
import folium
from geopy.geocoders import Nominatim
from geopy.exc import GeocoderTimedOut, GeocoderServiceError

app = Flask(__name__)

USER_AGENT = os.environ.get("GEOPY_USER_AGENT", "route_mapper_render")
FRAME_ANCESTORS = os.environ.get(
    "FRAME_ANCESTORS",
    "https://sites.google.com https://*.google.com https://*.googleusercontent.com"
)

@app.after_request
def add_frame_headers(response):
    # Permitir embedding en Google Sites / dominios google
    csp_value = f"frame-ancestors 'self' {FRAME_ANCESTORS};"
    response.headers["Content-Security-Policy"] = csp_value
    # NO añadimos X-Frame-Options (podría bloquear el embedding)
    return response

def get_coordinates(address, timeout=10):
    geolocator = Nominatim(user_agent=USER_AGENT, timeout=timeout)
    try:
        location = geolocator.geocode(address)
        if location:
            return (location.latitude, location.longitude)
    except (GeocoderTimedOut, GeocoderServiceError) as e:
        app.logger.warning("Geocoding error: %s", e)
    return None

def get_route_osrm(start_coords, end_coords):
    if not start_coords or not end_coords:
        return None, None, None
    osrm_url = (
        f"https://router.project-osrm.org/route/v1/driving/"
        f"{start_coords[1]},{start_coords[0]};{end_coords[1]},{end_coords[0]}"
    )
    params = {"overview": "full", "geometries": "geojson"}
    try:
        resp = requests.get(osrm_url, params=params, timeout=15)
        resp.raise_for_status()
        data = resp.json()
        if "routes" in data and data["routes"]:
            coords = [(c[1], c[0]) for c in data["routes"][0]["geometry"]["coordinates"]]
            duration = data["routes"][0].get("duration")
            distance = data["routes"][0].get("distance")
            return coords, duration, distance
    except requests.RequestException as e:
        app.logger.warning("OSRM request error: %s", e)
    return None, None, None

def generate_stations_near_start(route_coords, num_stations=3, max_distance_meters=40):
    if not route_coords:
        return []
    stations = []
    for i in range(num_stations):
        point = route_coords[min(i, len(route_coords) - 1)]
        lat, lon = point
        lat_off = random.uniform(-max_distance_meters / 111000.0, max_distance_meters / 111000.0)
        lon_off = random.uniform(
            -max_distance_meters / (111000.0 * max(0.2, abs(lat) / 90.0 + 0.2)),
             max_distance_meters / (111000.0 * max(0.2, abs(lat) / 90.0 + 0.2)))
        stations.append((lat + lat_off, lon + lon_off))
    return stations

INDEX_HTML = """
<!doctype html>
<html>
<head><meta charset="utf-8"><title>Route Mapper</title></head>
<body>
  <h3>Route Mapper</h3>
  <form action="/map" method="get" target="_self">
    <input name="start" type="text" placeholder="Dirección inicial" required>
    <input name="end" type="text" placeholder="Dirección final" required>
    <select name="num_stations">
      <option value="2">2</option><option value="3" selected>3</option><option value="4">4</option>
    </select>
    <button type="submit">Generar</button>
  </form>
  <p>Nota: Nominatim y OSRM son servicios públicos con límites de uso.</p>
</body>
</html>
"""

@app.route("/")
def index():
    return render_template_string(INDEX_HTML)

@app.route("/map")
def map_view():
    start = request.args.get("start")
    end = request.args.get("end")
    try:
        num_stations = int(request.args.get("num_stations", 3))
    except ValueError:
        num_stations = 3

    if not start or not end:
        return abort(400, "Start and end are required")

    start_coords = get_coordinates(start)
    end_coords = get_coordinates(end)
    if not start_coords or not end_coords:
        return abort(404, "No se encontraron coordenadas para alguna dirección")

    route_coords, duration, distance = get_route_osrm(start_coords, end_coords)
    if not route_coords:
        return abort(500, "No se pudo obtener la ruta desde OSRM")

    m = folium.Map(location=start_coords, zoom_start=13)
    folium.PolyLine(route_coords, weight=6, opacity=0.8).add_to(m)
    folium.Marker(location=start_coords, popup="Inicio").add_to(m)
    folium.Marker(location=end_coords, popup="Destino").add_to(m)

    stations = generate_stations_near_start(route_coords, num_stations=num_stations)
    for i, s in enumerate(stations, 1):
        folium.CircleMarker(location=s, radius=6, popup=f"Estación {i}").add_to(m)

    tmp = tempfile.NamedTemporaryFile(suffix=".html", delete=False)
    tmp.close()
    m.save(tmp.name)
    return send_file(tmp.name, mimetype="text/html")

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
