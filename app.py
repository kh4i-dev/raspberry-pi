# -*- coding: utf-8 -*-
import json
import threading
import time
from flask import Flask, render_template, request, jsonify
from flask_sock import Sock

# --- BIẾN TRẠNG THÁI TRUNG TÂM ---
# Cập nhật tên sản phẩm theo yêu cầu mới
system_state = {
    "status": "Offline",
    "lanes": [
        {"name": "Sữa hộp", "status": "Chưa kết nối", "count": 0, "sensor": 1, "relay_grab": 1, "relay_push": 0},
        {"name": "Nước ngọt", "status": "Chưa kết nối", "count": 0, "sensor": 1, "relay_grab": 1, "relay_push": 0},
        {"name": "Nước suối", "status": "Chưa kết nối", "count": 0, "sensor": 1, "relay_grab": 1, "relay_push": 0}
    ]
}
state_lock = threading.Lock()
connected_clients = set()

# --- KHỞI TẠO FLASK ---
app = Flask(__name__)
sock = Sock(app)

# --- HÀM GIAO TIẾP WEBSOCKET ---
def broadcast(message):
    """Gửi dữ liệu tới tất cả các client đang kết nối qua WebSocket."""
    json_message = json.dumps(message)
    for client in list(connected_clients):
        try:
            client.send(json_message)
        except Exception:
            connected_clients.remove(client)

# --- CÁC ROUTE CỦA WEB SERVER ---
@app.route('/')
def index():
    """Route chính để hiển thị trang web dashboard."""
    return render_template('index.html')

@app.route('/update', methods=['POST'])
def update_from_pi():
    """API Endpoint để Raspberry Pi gửi dữ liệu trạng thái đầy đủ."""
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
    """API Endpoint mới để nhận các sự kiện log từ Pi."""
    log_data = request.get_json()
    if not log_data or 'log_type' not in log_data:
        return jsonify({"status": "error", "message": "Invalid log format"}), 400
    
    if 'timestamp' not in log_data:
        log_data['timestamp'] = time.strftime('%H:%M:%S')

    # Phát bản tin log tới tất cả các dashboard
    broadcast({"type": "log", **log_data})
    return jsonify({"status": "ok"})

@sock.route('/ws')
def ws(sock):
    """Route cho kết nối WebSocket."""
    connected_clients.add(sock)
    print(f"Client connected. Total clients: {len(connected_clients)}")
    try:
        sock.send(json.dumps({"type": "state_update", "state": system_state}))
        while True:
            sock.receive(timeout=60)
    except Exception:
        pass
    finally:
        connected_clients.remove(sock)
        print(f"Client disconnected. Total clients: {len(connected_clients)}")

# --- CHẠY CHƯƠNG TRÌNH ---
if __name__ == '__main__':
    print("🌐 Flask Dashboard Server is running on http://0.0.0.0:5000")
    app.run(host='0.0.0.0', port=5000)

