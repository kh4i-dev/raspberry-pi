# -*- coding: utf-8 -*-
import json
import threading
import time
from flask import Flask, render_template, request, jsonify
from flask_sock import Sock

# --- BIẾN TRẠNG THÁI TRUNG TÂM ---
system_state = {
    "status": "Offline",
    "lanes": [
        {"name": "Sữa hộp", "status": "Chưa kết nối", "count": 0, "sensor": 1, "relay_grab": 1, "relay_push": 0},
        {"name": "Nước ngọt", "status": "Chưa kết nối", "count": 0, "sensor": 1, "relay_grab": 1, "relay_push": 0},
        {"name": "Nước suối", "status": "Chưa kết nối", "count": 0, "sensor": 1, "relay_grab": 1, "relay_push": 0}
    ]
}
last_image_b64 = None # Biến mới để lưu ảnh gần nhất
state_lock = threading.Lock()
connected_clients = set()

# --- KHỞI TẠO FLASK ---
app = Flask(__name__)
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
    return jsonify({"status": "ok"})

@app.route('/log', methods=['POST'])
def log_from_pi():
    log_data = request.get_json()
    if not log_data or 'log_type' not in log_data:
        return jsonify({"status": "error", "message": "Invalid log format"}), 400
    if 'timestamp' not in log_data:
        log_data['timestamp'] = time.strftime('%H:%M:%S')
    broadcast({"type": "log", **log_data})
    return jsonify({"status": "ok"})

@app.route('/image_update', methods=['POST'])
def image_update_from_pi():
    """API Endpoint mới để nhận ảnh chụp từ Pi."""
    global last_image_b64
    data = request.get_json()
    if not data or 'image' not in data:
        return jsonify({"status": "error", "message": "Invalid image data"}), 400
    
    with state_lock:
        last_image_b64 = data['image']
    
    # Gửi ảnh mới tới tất cả các dashboard
    broadcast({"type": "image_update", "image": last_image_b64})
    return jsonify({"status": "ok"})

@sock.route('/ws')
def ws(sock):
    connected_clients.add(sock)
    print(f"Client connected. Total clients: {len(connected_clients)}")
    try:
        # Gửi trạng thái và ảnh gần nhất (nếu có) khi client kết nối
        sock.send(json.dumps({"type": "state_update", "state": system_state}))
        if last_image_b64:
            sock.send(json.dumps({"type": "image_update", "image": last_image_b64}))
        while True:
            sock.receive(timeout=60)
    except Exception:
        pass
    finally:
        connected_clients.remove(sock)
        print(f"Client disconnected. Total clients: {len(connected_clients)}")

if __name__ == '__main__':
    print("🌐 Flask Dashboard Server is running on http://0.0.0.0:5000")
    app.run(host='0.0.0.0', port=5000)

