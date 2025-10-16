# -*- coding: utf-8 -*-
import os
import time
import json
import threading
import random
from flask import Flask, render_template, Response
from flask_sock import Sock

# --- CẤU HÌNH CHẾ ĐỘ ---
LOCAL_MODE = os.getenv("LOCAL_MODE", "True") == "True"
print(f"[SYSTEM] LOCAL_MODE = {LOCAL_MODE}")

if LOCAL_MODE:
    try:
        import RPi.GPIO as GPIO
        import cv2
    except ImportError:
        print("[WARN] Không tìm thấy thư viện RPi.GPIO hoặc OpenCV. Vẫn tiếp tục chạy mô phỏng.")
        LOCAL_MODE = False

# --- CẤU HÌNH ---
CAMERA_INDEX = 0

# --- GPIO / MOCK ---
if LOCAL_MODE:
    GPIO.setmode(GPIO.BOARD)
    GPIO.setwarnings(False)

    RELAY_PINS = {
        0: {'grab': 11, 'push': 13},
        1: {'grab': 15, 'push': 16},
        2: {'grab': 18, 'push': 22}
    }
    SENSOR_PINS = {0: 29, 1: 31, 2: 33}

    for lane_pins in RELAY_PINS.values():
        GPIO.setup(lane_pins['grab'], GPIO.OUT)
        GPIO.setup(lane_pins['push'], GPIO.OUT)
        GPIO.output(lane_pins['grab'], GPIO.HIGH)
        GPIO.output(lane_pins['push'], GPIO.LOW)

    for pin in SENSOR_PINS.values():
        GPIO.setup(pin, GPIO.IN, pull_up_down=GPIO.PUD_UP)
else:
    RELAY_PINS = {0: {}, 1: {}, 2: {}}
    SENSOR_PINS = {0: 0, 1: 1, 2: 2}

# --- TRẠNG THÁI HỆ THỐNG ---
system_state = {
    "status": "Online",
    "lanes": [
        {"name": "Hộp Sữa", "status": "Sẵn sàng", "count": 0, "sensor": 1, "relay_grab": 1, "relay_push": 0},
        {"name": "Kim Loại", "status": "Sẵn sàng", "count": 0, "sensor": 1, "relay_grab": 1, "relay_push": 0},
        {"name": "Nhựa", "status": "Sẵn sàng", "count": 0, "sensor": 1, "relay_grab": 1, "relay_push": 0}
    ]
}
state_lock = threading.Lock()
connected_clients = set()
output_frame = None
frame_lock = threading.Lock()

# --- HÀM PHÁT BROADCAST ---
def broadcast(msg):
    json_msg = json.dumps(msg)
    for client in list(connected_clients):
        try:
            client.send(json_msg)
        except Exception:
            connected_clients.remove(client)

def update_and_broadcast_state():
    broadcast({"type": "state_update", "state": system_state})

# --- THREAD GIÁM SÁT CẢM BIẾN ---
def sensor_monitor_thread():
    previous_states = [-1, -1, -1]
    while True:
        for i in range(3):
            if LOCAL_MODE:
                current_state = GPIO.input(SENSOR_PINS[i])
            else:
                current_state = random.choice([0, 1])

            if current_state != previous_states[i]:
                previous_states[i] = current_state
                lane_name = system_state["lanes"][i]["name"]
                system_state["lanes"][i]["sensor"] = current_state
                broadcast({
                    "type": "log",
                    "log_type": "sensor",
                    "name": lane_name,
                    "status": current_state,
                    "timestamp": time.strftime("%H:%M:%S")
                })
                update_and_broadcast_state()
        time.sleep(0.7)

# --- LUỒNG MÔ PHỎNG PHÂN LOẠI ---
def fake_sorting_thread():
    while not LOCAL_MODE:
        lane_index = random.randint(0, 2)
        system_state["lanes"][lane_index]["count"] += 1
        broadcast({
            "type": "log",
            "log_type": "sort",
            "name": system_state["lanes"][lane_index]["name"],
            "count": system_state["lanes"][lane_index]["count"],
            "timestamp": time.strftime("%H:%M:%S")
        })
        update_and_broadcast_state()
        time.sleep(5)

# --- WEB SERVER ---
app = Flask(__name__)
sock = Sock(app)

@app.route('/')
def index():
    return render_template('index.html')

@app.route('/video_feed')
def video_feed():
    def generate_frames():
        global output_frame
        while True:
            if LOCAL_MODE and output_frame is not None:
                yield (b'--frame\r\n'
                       b'Content-Type: image/jpeg\r\n\r\n' + bytearray(output_frame) + b'\r\n')
            else:
                time.sleep(1)
    return Response(generate_frames(), mimetype='multipart/x-mixed-replace; boundary=frame')

@sock.route('/ws')
def ws(sock):
    connected_clients.add(sock)
    try:
        sock.send(json.dumps({"type": "state_update", "state": system_state}))
        while True:
            sock.receive(timeout=1)
    except Exception:
        pass
    finally:
        connected_clients.remove(sock)

# --- MAIN ---
if __name__ == '__main__':
    print("🚀 Flask Dashboard đang chạy...")
    threading.Thread(target=sensor_monitor_thread, daemon=True).start()
    threading.Thread(target=fake_sorting_thread, daemon=True).start()
    app.run(host='0.0.0.0', port=5000)
