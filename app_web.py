# app_web.py
import os
import tempfile
import random
import requests
from flask import Flask, request, render_template, send_file, abort, make_response
import folium
from geopy.geocoders import Nominatim
from geopy.exc import GeocoderTimedOut, GeocoderServiceError

app = Flask(__name__)

USER_AGENT = os.environ.get("GEOPY_USER_AGENT", "route_mapper_web")
FRAME_ANCESTORS = os.environ.get(
    "FRAME_ANCESTORS",
    "https://sites.google.com https://*.google.com https://*.googleusercontent.com"
)

@app.after_request
def add_frame_headers(response):
    csp_value = f"frame-ancestors 'self' {FRAME_ANCESTORS};"
    response.headers["Content-Security-Policy"] = csp_value
    return response

def get_coordinates(address, timeout=10):
    geolocator = Nominatim(user_agent=USER_AGENT, timeout=timeout)
    try:
        location = geolocator.geocode(address)
        if location:
            return (location.latitude, location.longitude)
    except (GeocoderTimedOut, GeocoderServiceError):
        return None
    return None

def get_route(start_coords, end_coords):
    if not start_coords or not end_coords:
        return None, None, None
    osrm_url = (
        f"https://router.project-osrm.org/route/v1/driving/"
        f"{start_coords[1]},{start_coords[0]};{end_coords[1]},{end_coords[0]}"
    )
    params = {"overview": "full", "geometries": "geojson"}
    try:
        r = requests.get(osrm_url, params=params, timeout=15)
        r.raise_for_status()
        data = r.json()
        if "routes" in data and data["routes"]:
            coords = [(c[1], c[0]) for c in data["routes"][0]["geometry"]["coordinates"]]
            duration = data["routes"][0].get("duration")
            distance = data["routes"][0].get("distance")
            return coords, duration, distance
    except requests.RequestException:
        return None, None, None
    return None, None, None

def generate_stations_near_start(route_coords, num_stations=3, max_distance_meters=40):
    if not route_coords:
        return []
    stations = []
    for i in range(num_stations):
        point = route_coords[min(i, len(route_coords)-1)]
        lat, lon = point
        lat_off = random.uniform(-max_distance_meters/111000.0, max_distance_meters/111000.0)
        lon_off = random.uniform(-max_distance_meters/(111000.0*max(0.2, abs(lat)/90.0+0.2)),
                                 max_distance_meters/(111000.0*max(0.2, abs(lat)/90.0+0.2)))
        stations.append((lat+lat_off, lon+lon_off))
    return stations

@app.route("/", methods=["GET"])
def index():
    # Render template simple; si no usas templates, puedes devolver un simple form
    return """
    <!doctype html><html><head><meta charset='utf-8'><title>Route Mapper</title></head><body>
    <h3>Route Mapper (Web)</h3>
    <form action="/map" method="get">
      <input name="start" placeholder="Dirección inicial" required>
      <input name="end" placeholder="Dirección final" required>
      <select name="num_stations"><option>2</option><option selected>3</option><option>4</option></select>
      <button type="submit">Generar</button>
    </form>
    <p>Nota: Nominatim y OSRM tienen límites.</p>
    </body></html>
    """

@app.route("/map")
def map_view():
    start = request.args.get("start")
    end = request.args.get("end")
    try:
        num_stations = int(request.args.get("num_stations", 3))
    except ValueError:
        num_stations = 3
    if not start or not end:
        return abort(400, "Start and end required")
    start_coords = get_coordinates(start)
    end_coords = get_coordinates(end)
    if not start_coords or not end_coords:
        return abort(404, "No coordinates")
    route_coords, duration, distance = get_route(start_coords, end_coords)
    if not route_coords:
        return abort(500, "No route")
    m = folium.Map(location=start_coords, zoom_start=14)
    folium.PolyLine(route_coords, weight=6, opacity=0.8).add_to(m)
    folium.Marker(start_coords, popup="Inicio").add_to(m)
    folium.Marker(end_coords, popup="Destino").add_to(m)
    stations = generate_stations_near_start(route_coords, num_stations=num_stations)
    for i, s in enumerate(stations, 1):
        folium.CircleMarker(location=s, radius=6, popup=f"Estación {i}").add_to(m)
    tmp = tempfile.NamedTemporaryFile(suffix=".html", delete=False)
    tmp.close()
    m.save(tmp.name)
    resp = make_response(send_file(tmp.name, mimetype="text/html"))
    return resp

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)))
