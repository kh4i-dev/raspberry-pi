# -*- coding: utf-8 -*-
import json
import threading
import time
import logging
from logging.handlers import RotatingFileHandler
import os
from flask import Flask, render_template, request, jsonify
from flask_sock import Sock

# --- CẤU HÌNH ---
STATE_FILE = 'dashboard_state.json'

# --- CÀI ĐẶT LOGGING ---
log_handler = RotatingFileHandler('dashboard.log', maxBytes=5*1024*1024, backupCount=2)
log_formatter = logging.Formatter('%(asctime)s - %(levelname)s - %(message)s')
log_handler.setFormatter(log_formatter)

# --- HÀM LƯU/TẢI TRẠNG THÁI ---
def get_default_state():
    """Trả về trạng thái mặc định ban đầu."""
    return {
        "status": "Offline",
        "lanes": [
            {"name": "Loại 1", "status": "Chưa kết nối", "count": 0, "sensor": 1, "relay_grab": 1, "relay_push": 0},
            {"name": "Loại 2", "status": "Chưa kết nối", "count": 0, "sensor": 1, "relay_grab": 1, "relay_push": 0},
            {"name": "Loại 3", "status": "Chưa kết nối", "count": 0, "sensor": 1, "relay_grab": 1, "relay_push": 0}
        ]
    }

def save_state(state):
    """Lưu trạng thái hiện tại vào file JSON."""
    try:
        with open(STATE_FILE, 'w') as f:
            json.dump(state, f, indent=4)
    except IOError as e:
        app.logger.error(f"Could not write state to file {STATE_FILE}: {e}")

def load_state():
    """Tải trạng thái từ file JSON. Nếu file không tồn tại hoặc lỗi, trả về trạng thái mặc định."""
    if not os.path.exists(STATE_FILE):
        return get_default_state()
    try:
        with open(STATE_FILE, 'r') as f:
            return json.load(f)
    except (json.JSONDecodeError, IOError):
        app.logger.error(f"Could not read or parse {STATE_FILE}. Using default state.")
        return get_default_state()

# --- KHỞI TẠO BIẾN TRẠNG THÁI TỪ FILE ---
system_state = load_state()
last_image_b64 = None
state_lock = threading.Lock()
connected_clients = set()

# --- KHỞI TẠO FLASK ---
app = Flask(__name__)
app.logger.addHandler(log_handler)
app.logger.setLevel(logging.INFO)
sock = Sock(app)

# --- HÀM GIAO TIẾP WEBSOCKET ---
def broadcast(message):
    json_message = json.dumps(message)
    for client in list(connected_clients):
        try:
            client.send(json_message)
        except Exception:
            connected_clients.remove(client)

# --- CÁC ROUTE CỦA WEB SERVER ---
@app.route('/')
def index():
    return render_template('index.html')

@app.route('/update', methods=['POST'])
def update_from_pi():
    # Thêm token-based security
    secret_token = "pi-secret-key" # Đảm bảo token này khớp với token trên Pi
    if request.headers.get("X-Token") != secret_token:
        app.logger.warning("Unauthorized attempt to update state.")
        return jsonify({"status": "error", "message": "Unauthorized"}), 403
    
    global system_state
    data = request.get_json()
    if not data:
        return jsonify({"status": "error", "message": "Invalid JSON"}), 400
    with state_lock:
        system_state = data
        save_state(system_state)
    broadcast({"type": "state_update", "state": system_state})
    # app.logger.info(f"State update received from Pi and saved. Status: {data.get('status')}") # Giảm log cho đỡ nhiễu
    return jsonify({"status": "ok"})

@app.route('/log', methods=['POST'])
def log_from_pi():
    secret_token = "pi-secret-key"
    if request.headers.get("X-Token") != secret_token:
        return jsonify({"status": "error", "message": "Unauthorized"}), 403

    log_data = request.get_json()
    if not log_data or 'log_type' not in log_data:
        return jsonify({"status": "error", "message": "Invalid log format"}), 400
    if 'timestamp' not in log_data:
        log_data['timestamp'] = time.strftime('%H:%M:%S')
    broadcast({"type": "log", **log_data})
    app.logger.info(f"Log received: {log_data}")
    return jsonify({"status": "ok"})

@app.route('/image_update', methods=['POST'])
def image_update_from_pi():
    secret_token = "pi-secret-key"
    if request.headers.get("X-Token") != secret_token:
        return jsonify({"status": "error", "message": "Unauthorized"}), 403
        
    global last_image_b64
    data = request.get_json()
    if not data or 'image' not in data:
        return jsonify({"status": "error", "message": "Invalid image data"}), 400
    with state_lock:
        last_image_b64 = data['image']
    broadcast({"type": "image_update", "image": last_image_b64})
    app.logger.info("Image received from Pi.")
    return jsonify({"status": "ok"})

@app.route('/reset_counts', methods=['POST'])
def reset_counts():
    with state_lock:
        for lane in system_state['lanes']:
            lane['count'] = 0
        save_state(system_state)
        app.logger.warning("All counters have been reset by a user and state has been saved.")
        broadcast({"type": "state_update", "state": system_state})
    return jsonify({"status": "ok", "message": "Counters reset successfully."})

@sock.route('/ws')
def ws(sock):
    connected_clients.add(sock)
    app.logger.info(f"Dashboard client connected. Total clients: {len(connected_clients)}")
    try:
        with state_lock:
            sock.send(json.dumps({"type": "state_update", "state": system_state}))
            if last_image_b64:
                sock.send(json.dumps({"type": "image_update", "image": last_image_b64}))
        while True:
            sock.receive(timeout=60)
    except Exception:
        pass
    finally:
        connected_clients.remove(sock)
        app.logger.info(f"Dashboard client disconnected. Total clients: {len(connected_clients)}")

if __name__ == '__main__':
    app.logger.info("Starting Flask Dashboard Server...")
    app.run(host='0.0.0.0', port=5000)

