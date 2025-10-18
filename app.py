# -*- coding: utf-8 -*-
import json
import threading
import time
import logging
from logging.handlers import RotatingFileHandler
import os
from flask import Flask, render_template, request, jsonify
from flask_sock import Sock
from collections import deque

# --- CẤU HÌNH ---
STATE_FILE = 'dashboard_state.json'
COMMAND_QUEUE = deque(maxlen=10)

# --- CÀI ĐẶT LOGGING ---
log_handler = RotatingFileHandler('dashboard.log', maxBytes=5*1024*1024, backupCount=2)
log_formatter = logging.Formatter('%(asctime)s - %(levelname)s - %(message)s')
log_handler.setFormatter(log_formatter)

# --- HÀM LƯU/TẢI TRẠNG THÁI ---
def get_default_state():
    return {
        "status": "Offline",
        "lanes": [
            {"name": "Loại 1", "status": "Chưa kết nối", "count": 0, "sensor": 1, "relay_grab": 1, "relay_push": 0},
            {"name": "Loại 2", "status": "Chưa kết nối", "count": 0, "sensor": 1, "relay_grab": 1, "relay_push": 0},
            {"name": "Loại 3", "status": "Chưa kết nối", "count": 0, "sensor": 1, "relay_grab": 1, "relay_push": 0}
        ],
        "pi_config": {
            "camera_enabled": True,
            "operating_mode": "normal" 
        },
        "timing_config": {
            "cycle_delay": 0.3 # Đồng bộ giá trị mặc định
        }
    }

def save_state(state):
    try:
        with open(STATE_FILE, 'w') as f: json.dump(state, f, indent=4)
    except IOError as e:
        app.logger.error(f"Could not write state to file {STATE_FILE}: {e}")

def load_state():
    if not os.path.exists(STATE_FILE): return get_default_state()
    try:
        with open(STATE_FILE, 'r') as f:
            state = json.load(f)
            if 'pi_config' not in state: state['pi_config'] = get_default_state()['pi_config']
            if 'timing_config' not in state: state['timing_config'] = get_default_state()['timing_config']
            state.pop('manual_relays', None)
            return state
    except (json.JSONDecodeError, IOError):
        app.logger.error(f"Could not read/parse {STATE_FILE}. Using default state.")
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
        try: client.send(json_message)
        except Exception: connected_clients.remove(client)

# --- CÁC ROUTE CỦA WEB SERVER ---
@app.route('/')
def index(): return render_template('index.html')

@app.route('/update', methods=['POST'])
def update_from_pi():
    secret_token = "pi-secret-key"
    if request.headers.get("X-Token") != secret_token: return jsonify({"status": "error", "message": "Unauthorized"}), 403
    global system_state
    data = request.get_json()
    if not data: return jsonify({"status": "error", "message": "Invalid JSON"}), 400
    command_to_pi = None
    if COMMAND_QUEUE: command_to_pi = COMMAND_QUEUE.popleft()
    with state_lock:
        # Cập nhật toàn bộ state từ Pi vì Pi là nguồn tin cậy duy nhất
        system_state.update(data)
        save_state(system_state)
    broadcast({"type": "state_update", "state": system_state})
    return jsonify({"status": "ok", "command": command_to_pi})

@app.route('/log', methods=['POST'])
def log_from_pi():
    secret_token = "pi-secret-key"
    if request.headers.get("X-Token") != secret_token: return jsonify({"status": "error", "message": "Unauthorized"}), 403
    log_data = request.get_json()
    if not log_data: return jsonify({"status": "error", "message": "Invalid log format"}), 400
    log_data['timestamp'] = time.strftime('%H:%M:%S')
    broadcast({"type": "log", **log_data})
    app.logger.info(f"Log received: {log_data}")
    return jsonify({"status": "ok"})

@app.route('/image_update', methods=['POST'])
def image_update_from_pi():
    secret_token = "pi-secret-key"
    if request.headers.get("X-Token") != secret_token: return jsonify({"status": "error", "message": "Unauthorized"}), 403
    global last_image_b64
    data = request.get_json()
    if not data or 'image' not in data: return jsonify({"status": "error", "message": "Invalid image data"}), 400
    with state_lock: last_image_b64 = data['image']
    broadcast({"type": "image_update", "image": last_image_b64})
    return jsonify({"status": "ok"})

@app.route('/reset_counts', methods=['POST'])
def reset_counts():
    with state_lock:
        for lane in system_state['lanes']: lane['count'] = 0
        save_state(system_state)
        app.logger.warning("Counters have been reset by user.")
        broadcast({"type": "state_update", "state": system_state})
    return jsonify({"status": "ok"})

@app.route('/test_command', methods=['POST'])
def test_command():
    command = request.get_json()
    if command and 'type' in command:
        COMMAND_QUEUE.append(command)
        app.logger.info(f"Command queued: {command}")
        with state_lock:
            if command['type'] == 'set_mode': 
                system_state['pi_config']['operating_mode'] = command['mode']
            elif command['type'] == 'toggle_camera': 
                system_state['pi_config']['camera_enabled'] = command['enabled']
            save_state(system_state)
            broadcast({"type": "state_update", "state": system_state})
        return jsonify({"status": "ok", "message": "Command queued."})
    return jsonify({"status": "error", "message": "Invalid command."}), 400

@sock.route('/ws')
def ws(sock):
    connected_clients.add(sock)
    app.logger.info(f"Client connected. Total: {len(connected_clients)}")
    try:
        with state_lock:
            sock.send(json.dumps({"type": "state_update", "state": system_state}))
            if last_image_b64: sock.send(json.dumps({"type": "image_update", "image": last_image_b64}))
        while True: sock.receive(timeout=60)
    except Exception: pass
    finally:
        connected_clients.remove(sock)
        app.logger.info(f"Client disconnected. Total: {len(connected_clients)}")

if __name__ == '__main__':
    app.logger.info("Starting Flask Dashboard Server...")
    app.run(host='0.0.0.0', port=5000)

