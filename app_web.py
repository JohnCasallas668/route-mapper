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

# Optional OpenAI usage
try:
    import openai
except Exception:
    openai = None

app = Flask(__name__, template_folder="templates", static_folder="static")

USER_AGENT = os.environ.get("GEOPY_USER_AGENT", "route_mapper_web_app/1.0")
FRAME_ANCESTORS = os.environ.get(
    "FRAME_ANCESTORS",
    "https://sites.google.com https://*.google.com https://*.googleusercontent.com"
)

OPENAI_KEY = os.environ.get("OPENAI_API_KEY")

@app.after_request
def add_frame_headers(response):
    csp_value = f"frame-ancestors 'self' {FRAME_ANCESTORS};"
    response.headers["Content-Security-Policy"] = csp_value
    response.headers["X-Frame-Options"] = "ALLOW-FROM https://sites.google.com"
    return response

def get_coordinates(address, timeout=10):
    geolocator = Nominatim(user_agent=USER_AGENT, timeout=timeout)
    try:
        loc = geolocator.geocode(address)
        if loc:
            return (loc.latitude, loc.longitude)
        else:
            print(f"Geocoding failed for address: {address}")
            return None
    except (GeocoderTimedOut, GeocoderServiceError) as e:
        print(f"Geocoding error: {e}")
        return None
    except Exception as e:
        print(f"Unexpected geocoding error: {e}")
        return None

def get_route(start_coords, end_coords):
    if not start_coords or not end_coords:
        print("Missing coordinates for route")
        return None, None, None
    
    osrm_url = (
        f"https://router.project-osrm.org/route/v1/driving/"
        f"{start_coords[1]},{start_coords[0]};{end_coords[1]},{end_coords[0]}"
    )
    params = {"overview": "full", "geometries": "geojson"}
    
    try:
        print(f"Requesting route from OSRM: {osrm_url}")
        r = requests.get(osrm_url, params=params, timeout=15)
        r.raise_for_status()
        data = r.json()
        
        if "routes" in data and data["routes"]:
            route_data = data["routes"][0]
            coords = [(c[1], c[0]) for c in route_data["geometry"]["coordinates"]]
            duration = route_data.get("duration")
            distance = route_data.get("distance")
            print(f"Route found: {len(coords)} points, duration: {duration}, distance: {distance}")
            return coords, duration, distance
        else:
            print("No route found in OSRM response")
            return None, None, None
            
    except requests.RequestException as e:
        print(f"OSRM request error: {e}")
        return None, None, None
    except Exception as e:
        print(f"Unexpected route error: {e}")
        return None, None, None

def generate_stations_near_start(route_coords, num_stations=3, max_distance_meters=40):
    if not route_coords:
        return []
    
    stations = []
    placement_points = min(5, len(route_coords))
    
    for i in range(num_stations):
        point_idx = min(i, placement_points - 1)
        point = route_coords[point_idx]
        lat, lon = point
        
        lat_offset = random.uniform(-max_distance_meters/111000.0, max_distance_meters/111000.0)
        lon_scale = max(0.3, abs(lat)/90.0 + 0.3)
        lon_offset = random.uniform(-max_distance_meters/(111000.0*lon_scale), 
                                   max_distance_meters/(111000.0*lon_scale))
        
        stations.append((lat + lat_offset, lon + lon_offset))
    
    return stations

@app.route("/", methods=["GET"])
def index():
    return render_template("index.html")

@app.route("/health")
def health_check():
    return jsonify({"status": "healthy", "message": "Route Mapper is running"})

@app.route("/api/route")
def api_route():
    start = request.args.get("start", "").strip()
    end = request.args.get("end", "").strip()
    
    try:
        num_stations = int(request.args.get("num_stations", 3))
    except ValueError:
        num_stations = 3

    if not start or not end:
        return jsonify({"error": "Se requieren direcciones de inicio y fin"}), 400

    print(f"Processing route from '{start}' to '{end}'")
    
    start_coords = get_coordinates(start)
    end_coords = get_coordinates(end)
    
    if not start_coords:
        return jsonify({"error": f"No se pudieron encontrar coordenadas para: {start}"}), 404
    if not end_coords:
        return jsonify({"error": f"No se pudieron encontrar coordenadas para: {end}"}), 404

    route_coords, duration, distance = get_route(start_coords, end_coords)
    
    if not route_coords:
        return jsonify({"error": "No se pudo calcular la ruta entre las ubicaciones especificadas"}), 500

    stations = generate_stations_near_start(route_coords, num_stations=num_stations)
    
    response_data = {
        "start": {"lat": start_coords[0], "lng": start_coords[1]},
        "end": {"lat": end_coords[0], "lng": end_coords[1]},
        "route": [{"lat": lat, "lng": lng} for lat, lng in route_coords],
        "stations": [{"lat": lat, "lng": lng} for lat, lng in stations],
        "duration_seconds": duration,
        "distance_meters": distance
    }
    
    return jsonify(response_data)

@app.route("/map")
def map_view():
    start = request.args.get("start", "").strip()
    end = request.args.get("end", "").strip()
    
    try:
        num_stations = int(request.args.get("num_stations", 3))
    except ValueError:
        num_stations = 3

    if not start or not end:
        return "Se requieren direcciones de inicio y fin", 400

    start_coords = get_coordinates(start)
    end_coords = get_coordinates(end)
    
    if not start_coords or not end_coords:
        return "No se pudieron encontrar las ubicaciones", 404

    route_coords, duration, distance = get_route(start_coords, end_coords)
    
    if not route_coords:
        return "No se pudo calcular la ruta", 500

    # Create map
    m = folium.Map(location=start_coords, zoom_start=13)
    folium.PolyLine(route_coords, weight=6, opacity=0.8, color='blue').add_to(m)
    folium.Marker(start_coords, popup="Inicio", tooltip="Inicio", icon=folium.Icon(color='green')).add_to(m)
    folium.Marker(end_coords, popup="Destino", tooltip="Destino", icon=folium.Icon(color='red')).add_to(m)
    
    stations = generate_stations_near_start(route_coords, num_stations=num_stations)
    for i, station in enumerate(stations, 1):
        folium.CircleMarker(
            location=station, 
            radius=8, 
            popup=f"Estación {i}", 
            tooltip=f"Estación {i}",
            color='orange',
            fill=True
        ).add_to(m)

    tmp = tempfile.NamedTemporaryFile(suffix=".html", delete=False)
    tmp.close()
    m.save(tmp.name)
    
    # Schedule file deletion
    def delete_temp_file(path, delay=30):
        time.sleep(delay)
        try:
            if os.path.exists(path):
                os.remove(path)
        except Exception:
            pass
    
    threading.Thread(target=delete_temp_file, args=(tmp.name, 30), daemon=True).start()
    
    return send_file(tmp.name, mimetype="text/html")

@app.route("/api/ai", methods=["POST"])
def api_ai():
    if not OPENAI_KEY:
        return jsonify({"error": "OpenAI API key no configurada"}), 500
        
    data = request.json or {}
    prompt = data.get("prompt", "").strip()
    
    if not prompt:
        return jsonify({"error": "Se requiere un prompt"}), 400

    try:
        headers = {
            "Authorization": f"Bearer {OPENAI_KEY}",
            "Content-Type": "application/json"
        }
        body = {
            "model": "gpt-4o-mini",
            "messages": [{"role": "user", "content": prompt}],
            "max_tokens": 300
        }
        
        response = requests.post(
            "https://api.openai.com/v1/chat/completions",
            headers=headers,
            json=body,
            timeout=15
        )
        response.raise_for_status()
        
        result = response.json()
        text = result["choices"][0]["message"]["content"]
        
        return jsonify({"result": text})
        
    except Exception as e:
        return jsonify({"error": f"Error en IA: {str(e)}"}), 500

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)), debug=False)