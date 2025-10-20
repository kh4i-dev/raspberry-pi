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
PI_TIMEOUT = 15 # Số giây trước khi coi Pi là offline

# --- CÀI ĐẶT LOGGING ---
log_handler = RotatingFileHandler('dashboard.log', maxBytes=5*1024*1024, backupCount=2)
log_formatter = logging.Formatter('%(asctime)s - %(levelname)s - %(message)s')
log_handler.setFormatter(log_formatter)

# --- HÀM LƯU/TẢI TRẠNG THÁI ---
def get_default_state():
    return {
        "status": "Offline", # Trạng thái ban đầu luôn là Offline
        "lanes": [
            {"name": "Loại 1", "status": "Chưa kết nối", "count": 0, "sensor": 1, "relay_grab": 1, "relay_push": 0},
            {"name": "Loại 2", "status": "Chưa kết nối", "count": 0, "sensor": 1, "relay_grab": 1, "relay_push": 0},
            {"name": "Loại 3", "status": "Chưa kết nối", "count": 0, "sensor": 1, "relay_grab": 1, "relay_push": 0}
        ],
        "pi_config": { "camera_enabled": True, "operating_mode": "normal" },
        "timing_config": { "cycle_delay": 1.0 }
    }

def save_state(state):
    try:
        with open(STATE_FILE, 'w') as f: json.dump(state, f, indent=4)
    except IOError as e:
        app.logger.error(f"Could not write state to file {STATE_FILE}: {e}")

def load_state():
    if not os.path.exists(STATE_FILE):
        state = get_default_state()
        save_state(state) # Tạo file state lần đầu
        return state
    try:
        with open(STATE_FILE, 'r') as f:
            state = json.load(f)
            state['status'] = 'Offline' # Luôn đặt là offline khi khởi động
            for lane in state['lanes']: lane['status'] = 'Chưa kết nối'
            return state
    except (json.JSONDecodeError, IOError):
        app.logger.error(f"Could not read/parse {STATE_FILE}. Using default state.")
        return get_default_state()

# --- KHỞI TẠO BIẾN TRẠNG THÁI TỪ FILE ---
system_state = load_state()
last_image_b64 = None
last_pi_heartbeat = 0
state_lock = threading.Lock()
connected_clients = set()

# --- KHỞI TẠO FLASK ---
app = Flask(__name__)
app.logger.addHandler(log_handler)
app.logger.setLevel(logging.INFO)
sock = Sock(app)

# --- HÀM GIAO TIẾP WEBSOCKET & HEARTBEAT---
def broadcast(message):
    json_message = json.dumps(message)
    for client in list(connected_clients):
        try: client.send(json_message)
        except Exception: connected_clients.remove(client)

def check_pi_heartbeat():
    """Luồng kiểm tra kết nối từ Pi."""
    global system_state
    while True:
        with state_lock:
            if system_state['status'] == 'Online' and (time.time() - last_pi_heartbeat > PI_TIMEOUT):
                app.logger.warning(f"Pi timed out. Last heartbeat was {time.time() - last_pi_heartbeat:.1f}s ago.")
                system_state['status'] = 'Offline'
                for lane in system_state['lanes']: lane['status'] = 'Mất kết nối'
                save_state(system_state)
                broadcast({"type": "state_update", "state": system_state})
        time.sleep(5)

# --- CÁC ROUTE CỦA WEB SERVER ---
@app.route('/')
def index(): return render_template('index.html')

@app.route('/update', methods=['POST'])
def update_from_pi():
    secret_token = "pi-secret-key"
    if request.headers.get("X-Token") != secret_token: return jsonify({"status": "error", "message": "Unauthorized"}), 403
    global system_state, last_pi_heartbeat
    data = request.get_json()
    if not data: return jsonify({"status": "error", "message": "Invalid JSON"}), 400
    command_to_pi = None
    if COMMAND_QUEUE: command_to_pi = COMMAND_QUEUE.popleft()
    with state_lock:
        system_state.update(data)
        # Nếu Pi vừa kết nối lại, cập nhật trạng thái
        if system_state['status'] != 'Online':
             app.logger.info("Pi reconnected.")
        system_state['status'] = 'Online'
        last_pi_heartbeat = time.time()
        save_state(system_state)
    broadcast({"type": "state_update", "state": system_state})
    return jsonify({"status": "ok", "command": command_to_pi})

# Các route còn lại (/log, /image_update, /reset_counts, /test_command, /ws) giữ nguyên
# ... (Phần còn lại của code giống hệt phiên bản trước) ...
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
            elif command['type'] == 'update_timing_config':
                system_state['timing_config']['cycle_delay'] = command.get('delay', 1.0)
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
    # Bắt đầu luồng kiểm tra heartbeat
    threading.Thread(target=check_pi_heartbeat, daemon=True).start()
    app.logger.info("Starting Flask Dashboard Server with Pi Heartbeat Check...")
    # Không dùng app.run() khi deploy với Gunicorn
    # app.run(host='0.0.0.0', port=5000)

