# app_web.py
import os
import tempfile
import random
import json
import requests
import threading
import time
from dotenv import load_dotenv
load_dotenv()
from flask import Flask, request, render_template, send_file, abort, make_response, jsonify
import folium
from geopy.geocoders import Nominatim
from geopy.exc import GeocoderTimedOut, GeocoderServiceError

# Optional OpenAI usage (install openai or use HTTP request)
try:
    import openai
except Exception:
    openai = None

app = Flask(__name__, template_folder="templates", static_folder="static")

USER_AGENT = os.environ.get("GEOPY_USER_AGENT", "route_mapper_web")
FRAME_ANCESTORS = os.environ.get(
    "FRAME_ANCESTORS",
    "https://sites.google.com https://*.google.com https://*.googleusercontent.com"
)

OPENAI_KEY = os.environ.get("OPENAI_API_KEY")  # set in Render if you want IA

@app.after_request
def add_frame_headers(response):
    csp_value = f"frame-ancestors 'self' {FRAME_ANCESTORS};"
    response.headers["Content-Security-Policy"] = csp_value
    return response

# ----- Reuse your geocode/route/stations functions -----
def get_coordinates(address, timeout=10):
    geolocator = Nominatim(user_agent=USER_AGENT, timeout=timeout)
    try:
        loc = geolocator.geocode(address)
        if loc:
            return (loc.latitude, loc.longitude)
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

# ----- Web frontend route (serves the page) -----
@app.route("/", methods=["GET"])
def index():
    return render_template("index.html")

# ----- JSON endpoint: route data for frontend (used by Leaflet JS) -----
@app.route("/api/route")
def api_route():
    start = request.args.get("start")
    end = request.args.get("end")
    try:
        num_stations = int(request.args.get("num_stations", 3))
    except ValueError:
        num_stations = 3
    if not start or not end:
        return jsonify({"error": "start and end required"}), 400
    start_coords = get_coordinates(start)
    end_coords = get_coordinates(end)
    if not start_coords or not end_coords:
        return jsonify({"error": "coordinates not found"}), 404
    route_coords, duration, distance = get_route(start_coords, end_coords)
    if not route_coords:
        return jsonify({"error": "no route available"}), 500
    stations = generate_stations_near_start(route_coords, num_stations=num_stations)
    return jsonify({
        "start": {"lat": start_coords[0], "lng": start_coords[1]},
        "end": {"lat": end_coords[0], "lng": end_coords[1]},
        "route": [{"lat": lat, "lng": lng} for lat,lng in route_coords],
        "stations": [{"lat": lat, "lng": lng} for lat,lng in stations],
        "duration_seconds": duration,
        "distance_meters": distance
    })

# ----- Optional: endpoint that returns full folium map HTML (backwards-compatible) -----
@app.route("/map")
def map_view():
    start = request.args.get("start")
    end = request.args.get("end")
    try:
        num_stations = int(request.args.get("num_stations", 3))
    except ValueError:
        num_stations = 3
    if not start or not end:
        return abort(400)
    start_coords = get_coordinates(start)
    end_coords = get_coordinates(end)
    if not start_coords or not end_coords:
        return abort(404)
    route_coords, duration, distance = get_route(start_coords, end_coords)
    if not route_coords:
        return abort(500)
    m = folium.Map(location=start_coords, zoom_start=14)
    folium.PolyLine(route_coords, weight=6, opacity=0.8).add_to(m)
    folium.Marker(start_coords, popup="Inicio").add_to(m)
    folium.Marker(end_coords, popup="Destino").add_to(m)
    stations = generate_stations_near_start(route_coords, num_stations=num_stations)
    for i, s in enumerate(stations,1):
        folium.CircleMarker(location=s, radius=6, popup=f"Estación {i}").add_to(m)
    tmp = tempfile.NamedTemporaryFile(suffix=".html", delete=False)
    tmp.close()
    m.save(tmp.name)
    response = make_response(send_file(tmp.name, mimetype="text/html"))

    def _del_later(path, delay=30):
        try:
            time.sleep(delay)
            if os.path.exists(path):
                os.remove(path)
        except Exception:
            pass
    threading.Thread(target=_del_later, args=(tmp.name,30), daemon=True).start()
    return response

# ----- AI endpoint (proxy to OpenAI). Requires OPENAI_API_KEY env var set in Render -----
@app.route("/api/ai", methods=["POST"])
def api_ai():
    data = request.json or {}
    prompt = data.get("prompt", "")
    if not prompt:
        return jsonify({"error":"prompt required"}), 400

    if not OPENAI_KEY:
        return jsonify({
            "error": "OpenAI API key not configured. Set OPENAI_API_KEY env var in Render."
        }), 500

    try:
        if openai:
            openai.api_key = OPENAI_KEY
            resp = openai.ChatCompletion.create(
                model="gpt-4o-mini",
                messages=[{"role":"user","content": prompt}],
                max_tokens=300,
            )
            text = resp.choices[0].message.content
            return jsonify({"result": text})
        else:
            headers = {"Authorization": f"Bearer {OPENAI_KEY}", "Content-Type":"application/json"}
            body = {"model":"gpt-4o-mini","messages":[{"role":"user","content":prompt}],"max_tokens":300}
            r = requests.post("https://api.openai.com/v1/chat/completions", headers=headers, json=body, timeout=15)
            r.raise_for_status()
            j = r.json()
            text = j["choices"][0]["message"]["content"]
            return jsonify({"result": text})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT",5000)), debug=False)
