# -*- coding: utf-8 -*-
import json
import threading
import time
import logging
from logging.handlers import RotatingFileHandler
from flask import Flask, render_template, request, jsonify
from flask_sock import Sock

# --- 1. CÀI ĐẶT LOGGING ---
# Thiết lập để ghi log ra file `dashboard.log`, tự động xoay vòng khi file lớn hơn 5MB
log_handler = RotatingFileHandler('dashboard.log', maxBytes=5*1024*1024, backupCount=2)
log_formatter = logging.Formatter('%(asctime)s - %(levelname)s - %(message)s')
log_handler.setFormatter(log_formatter)

# --- BIẾN TRẠNG THÁI TRUNG TÂM ---
system_state = {
    "status": "Offline",
    "lanes": [
        {"name": "Chai nhựa", "status": "Chưa kết nối", "count": 0, "sensor": 1, "relay_grab": 1, "relay_push": 0},
        {"name": "Lon nước", "status": "Chưa kết nối", "count": 0, "sensor": 1, "relay_grab": 1, "relay_push": 0},
        {"name": "Hộp sữa giấy", "status": "Chưa kết nối", "count": 0, "sensor": 1, "relay_grab": 1, "relay_push": 0}
    ]
}
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
    global system_state
    data = request.get_json()
    if not data:
        return jsonify({"status": "error", "message": "Invalid JSON"}), 400
    with state_lock:
        system_state = data
    broadcast({"type": "state_update", "state": system_state})
    app.logger.info(f"State update received from Pi. Status: {data.get('status')}")
    return jsonify({"status": "ok"})

@app.route('/log', methods=['POST'])
def log_from_pi():
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
    global last_image_b64
    data = request.get_json()
    if not data or 'image' not in data:
        return jsonify({"status": "error", "message": "Invalid image data"}), 400
    with state_lock:
        last_image_b64 = data['image']
    broadcast({"type": "image_update", "image": last_image_b64})
    app.logger.info("Image received from Pi.")
    return jsonify({"status": "ok"})

# --- 2. API ENDPOINT MỚI ĐỂ RESET BỘ ĐẾM ---
@app.route('/reset_counts', methods=['POST'])
def reset_counts():
    """Reset bộ đếm của tất cả các dây chuyền về 0."""
    with state_lock:
        for lane in system_state['lanes']:
            lane['count'] = 0
        app.logger.warning("All counters have been reset by a user.")
        # Gửi trạng thái mới nhất đến tất cả các client
        broadcast({"type": "state_update", "state": system_state})
    return jsonify({"status": "ok", "message": "Counters reset successfully."})

@sock.route('/ws')
def ws(sock):
    connected_clients.add(sock)
    app.logger.info(f"Dashboard client connected. Total clients: {len(connected_clients)}")
    try:
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

