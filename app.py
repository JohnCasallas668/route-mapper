# app.py - Versión unificada para compatibilidad
import os
import sys
import tempfile
import random
import requests
import folium
import threading
import time
from dotenv import load_dotenv
load_dotenv()

from geopy.geocoders import Nominatim
from geopy.exc import GeocoderTimedOut, GeocoderServiceError

# Check if running in web mode
WEB_MODE = os.environ.get('RENDER', False) or os.environ.get('FLASK_MODE') == 'web'

if not WEB_MODE:
    # Only import PyQt for desktop mode
    try:
        from PyQt5.QtWidgets import (
            QApplication, QWidget, QVBoxLayout, QLabel,
            QLineEdit, QPushButton, QMessageBox, QHBoxLayout, QDialog, QComboBox
        )
        from PyQt5.QtWebEngineWidgets import QWebEngineView
        from PyQt5.QtCore import QUrl, Qt
        PYQT_AVAILABLE = True
    except Exception:
        PYQT_AVAILABLE = False
else:
    PYQT_AVAILABLE = False

# Flask imports for web mode
from flask import Flask, request, send_file, render_template, abort, jsonify, make_response

# -----------------------------
# CORE FUNCTIONS (shared between desktop and web)
# -----------------------------
def get_coordinates(address, user_agent="route_mapper_app", timeout=10):
    """Devuelve (lat, lon) o None si no encuentra."""
    geolocator = Nominatim(user_agent=user_agent, timeout=timeout)
    try:
        location = geolocator.geocode(address)
        if location:
            return (location.latitude, location.longitude)
    except (GeocoderTimedOut, GeocoderServiceError) as e:
        print("Error geocoding:", e)
    return None

def get_route(start_coords, end_coords):
    """Llama a la API pública de OSRM y devuelve la ruta."""
    if not start_coords or not end_coords:
        return None, None, None

    osrm_url = (
        f"https://router.project-osrm.org/route/v1/driving/"
        f"{start_coords[1]},{start_coords[0]};{end_coords[1]},{end_coords[0]}"
    )
    params = {"overview": "full", "geometries": "geojson"}
    try:
        response = requests.get(osrm_url, params=params, timeout=15)
        response.raise_for_status()
        data = response.json()
        if "routes" in data and data["routes"]:
            route_coords = data["routes"][0]["geometry"]["coordinates"]
            duration = data["routes"][0]["duration"]
            distance = data["routes"][0]["distance"]
            return [(coord[1], coord[0]) for coord in route_coords], duration, distance
    except requests.RequestException as e:
        print("Error al pedir ruta OSRM:", e)
    return None, None, None

def generate_stations_near_start(route_coords, num_stations=3, max_distance_meters=30):
    """Genera estaciones simuladas cerca del inicio de la ruta."""
    if not route_coords or len(route_coords) == 0:
        return []

    stations = []
    for i in range(num_stations):
        point = route_coords[min(i, len(route_coords) - 1)]
        lat = point[0]
        lon = point[1]
        lat_offset = random.uniform(-max_distance_meters / 111000.0, max_distance_meters / 111000.0)
        lon_offset = random.uniform(-max_distance_meters / (111000.0 * max(0.5, abs(lat) / 90.0 + 0.5)),
                                    max_distance_meters / (111000.0 * max(0.5, abs(lat) / 90.0 + 0.5)))
        stations.append((lat + lat_offset, lon + lon_offset))
    return stations

# -----------------------------
# DESKTOP GUI (PyQt) - Only if not in web mode
# -----------------------------
if not WEB_MODE and PYQT_AVAILABLE:
    class DisabilitySelector(QDialog):
        def __init__(self):
            super().__init__()
            self.setWindowTitle("Seleccionar Discapacidad")
            self.setGeometry(100, 100, 600, 200)
            self.setStyleSheet("""
                QDialog {
                    background-color: #f5f5f5;
                    font-family: Arial, sans-serif;
                }
                QLabel {
                    font-size: 16px;
                    color: #333;
                    padding: 8px;
                }
                QComboBox {
                    padding: 8px;
                    font-size: 14px;
                }
                QPushButton {
                    background-color: #4CAF50;
                    color: white;
                    padding: 8px;
                    font-size: 14px;
                    border: none;
                    border-radius: 6px;
                }
                QPushButton:hover {
                    background-color: #45a049;
                }
            """)
            layout = QVBoxLayout()
            self.label = QLabel("Seleccione su tipo de discapacidad:")
            self.label.setAlignment(Qt.AlignCenter)
            self.comboBox = QComboBox()
            self.comboBox.addItems(["Movilidad reducida", "Visual", "Auditiva", "Otra"])
            self.okButton = QPushButton("Aceptar")
            self.okButton.clicked.connect(self.accept)
            layout.addStretch(1)
            layout.addWidget(self.label, alignment=Qt.AlignCenter)
            layout.addWidget(self.comboBox, alignment=Qt.AlignCenter)
            layout.addWidget(self.okButton, alignment=Qt.AlignCenter)
            layout.addStretch(1)
            self.setLayout(layout)

        def get_disability(self):
            return self.comboBox.currentText()

    class RouteMapperApp(QWidget):
        def __init__(self):
            super().__init__()
            self.setWindowTitle("Mapa de Ruta con Conductores Cercanos")
            self.setGeometry(100, 100, 1200, 800)
            self.disability_type = None
            self.temp_html_path = None
            self.initUI()

        def initUI(self):
            # Mostrar selector de discapacidad al inicio
            disability_dialog = DisabilitySelector()
            if disability_dialog.exec_() == QDialog.Accepted:
                self.disability_type = disability_dialog.get_disability()

            main_layout = QVBoxLayout()
            input_layout = QHBoxLayout()

            self.start_label = QLabel("Dirección inicial:")
            self.start_input = QLineEdit()
            self.end_label = QLabel("Dirección final:")
            self.end_input = QLineEdit()

            input_layout.addWidget(self.start_label)
            input_layout.addWidget(self.start_input)
            input_layout.addWidget(self.end_label)
            input_layout.addWidget(self.end_input)

            self.generate_button = QPushButton("Generar Mapa")
            self.generate_button.clicked.connect(self.generate_map)

            # Vista del mapa
            self.map_view = QWebEngineView()

            # Info label (tiempo y distancia)
            self.info_label = QLabel("")
            self.info_label.setAlignment(Qt.AlignCenter)
            self.info_label.setStyleSheet("""
                QLabel {
                    background-color: #ffffff;
                    border: 1px solid #ccc;
                    padding: 8px;
                    font-size: 14px;
                    border-radius: 6px;
                }
            """)

            main_layout.addLayout(input_layout)
            main_layout.addWidget(self.generate_button)
            main_layout.addWidget(self.info_label)
            main_layout.addWidget(self.map_view, stretch=1)

            self.setLayout(main_layout)

        def generate_map(self):
            start_addr = self.start_input.text().strip()
            end_addr = self.end_input.text().strip()

            if not start_addr or not end_addr:
                QMessageBox.warning(self, "Error", "Por favor ingrese direcciones de inicio y final.")
                return

            # Obtener coordenadas
            start_coords = get_coordinates(start_addr)
            if not start_coords:
                QMessageBox.critical(self, "Error", f"No se encontraron coordenadas para: {start_addr}")
                return

            end_coords = get_coordinates(end_addr)
            if not end_coords:
                QMessageBox.critical(self, "Error", f"No se encontraron coordenadas para: {end_addr}")
                return

            # Obtener ruta desde OSRM
            route_coords, duration, distance = get_route(start_coords, end_coords)
            if not route_coords:
                QMessageBox.critical(self, "Error", "No se pudo obtener la ruta desde el servicio de enrutamiento.")
                return

            # Crear el mapa centrado en el punto inicial
            m = folium.Map(location=start_coords, zoom_start=14)

            # Añadir línea de la ruta
            folium.PolyLine(route_coords, weight=6, opacity=0.8).add_to(m)

            # Añadir marcadores de inicio y fin
            folium.Marker(location=start_coords, popup="Inicio", tooltip="Inicio").add_to(m)
            folium.Marker(location=end_coords, popup="Destino", tooltip="Destino").add_to(m)

            # Generar estaciones cercanas simuladas
            stations = generate_stations_near_start(route_coords, num_stations=4, max_distance_meters=40)
            for idx, st in enumerate(stations, start=1):
                folium.CircleMarker(location=st,
                                    radius=6,
                                    popup=f"Estación {idx} (simulada)",
                                    tooltip=f"Estación {idx}").add_to(m)

            # Info de tiempo y distancia
            duration_min = duration / 60.0 if duration else None
            distance_km = distance / 1000.0 if distance else None
            info_text = "Discapacidad seleccionada: {}".format(self.disability_type or "No especificada")
            if duration_min is not None and distance_km is not None:
                info_text += f" — Distancia: {distance_km:.2f} km, Duración aprox.: {duration_min:.1f} min"

            self.info_label.setText(info_text)

            # Guardar mapa a un HTML temporal y cargarlo en QWebEngineView
            try:
                fd, path = tempfile.mkstemp(suffix=".html")
                os.close(fd)
                m.save(path)
                self.temp_html_path = path
                local_url = QUrl.fromLocalFile(path)
                self.map_view.setUrl(local_url)
            except Exception as e:
                QMessageBox.critical(self, "Error", f"No se pudo crear el archivo HTML del mapa: {e}")
                print("Error saving map HTML:", e)

        def closeEvent(self, event):
            # Intentar eliminar el HTML temporal al cerrar
            try:
                if self.temp_html_path and os.path.exists(self.temp_html_path):
                    os.remove(self.temp_html_path)
            except Exception:
                pass
            event.accept()

    def main_desktop():
        app = QApplication(sys.argv)
        window = RouteMapperApp()
        window.show()
        sys.exit(app.exec_())

# -----------------------------
# FLASK WEB APP
# -----------------------------
flask_app = Flask(__name__, template_folder="templates", static_folder="static")

FRAME_ANCESTORS_ENV = os.environ.get(
    "FRAME_ANCESTORS",
    "https://sites.google.com https://*.google.com https://*.googleusercontent.com"
)

@flask_app.after_request
def add_frame_headers(response):
    csp_value = f"frame-ancestors 'self' {FRAME_ANCESTORS_ENV};"
    response.headers["Content-Security-Policy"] = csp_value
    return response

@flask_app.route("/")
def index_web():
    return render_template("index.html")

@flask_app.route("/health")
def health_web():
    return jsonify({"status": "healthy", "mode": "web"})

@flask_app.route("/api/route")
def api_route_web():
    start = request.args.get("start", "").strip()
    end = request.args.get("end", "").strip()
    
    try:
        num_stations = int(request.args.get("num_stations", 3))
    except ValueError:
        num_stations = 3

    if not start or not end:
        return jsonify({"error": "Se requieren direcciones de inicio y fin"}), 400

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

@flask_app.route("/map")
def map_web():
    start = request.args.get("start", "").strip()
    end = request.args.get("end", "").strip()
    
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

    route_coords, duration, distance = get_route(start_coords, end_coords)
    
    if not route_coords:
        return abort(500, "No se pudo obtener la ruta desde OSRM")

    m = folium.Map(location=start_coords, zoom_start=14)
    folium.PolyLine(route_coords, weight=6, opacity=0.8).add_to(m)
    folium.Marker(location=start_coords, popup="Inicio", tooltip="Inicio").add_to(m)
    folium.Marker(location=end_coords, popup="Destino", tooltip="Destino").add_to(m)

    stations = generate_stations_near_start(route_coords, num_stations=num_stations, max_distance_meters=40)
    for idx, s in enumerate(stations, start=1):
        folium.CircleMarker(location=s, radius=6, popup=f"Estación {idx} (simulada)", tooltip=f"Estación {idx}").add_to(m)

    tmp = tempfile.NamedTemporaryFile(suffix=".html", delete=False)
    tmp.close()
    m.save(tmp.name)
    resp = make_response(send_file(tmp.name, mimetype="text/html"))
    
    def _del_later(path, delay=30):
        try:
            time.sleep(delay)
            if os.path.exists(path):
                os.remove(path)
        except Exception:
            pass
    
    threading.Thread(target=_del_later, args=(tmp.name, 30), daemon=True).start()
    return resp

# Expose both for compatibility
app = flask_app

# -----------------------------
# MAIN EXECUTION
# -----------------------------
def main():
    # Determine mode
    if len(sys.argv) > 1 and sys.argv[1].lower() in ("web", "server", "flask"):
        mode = "web"
    elif os.environ.get("RENDER") or os.environ.get("FLASK_MODE") == "web":
        mode = "web"
    else:
        mode = "desktop"

    if mode == "web":
        port = int(os.environ.get("PORT", 5000))
        flask_app.run(host="0.0.0.0", port=port)
    else:
        if not PYQT_AVAILABLE:
            print("PyQt no está disponible. Ejecutando en modo web...")
            port = int(os.environ.get("PORT", 5000))
            flask_app.run(host="0.0.0.0", port=port)
        else:
            main_desktop()

if __name__ == "__main__":
    main()