# -*- coding: utf-8 -*-
import os
import cv2
import time
import threading
import json
import random
from flask import Flask, render_template, Response
from flask_sock import Sock

# --- CẤU HÌNH CHẾ ĐỘ ---
LOCAL_MODE = os.getenv("LOCAL_MODE", "True") == "True"
CAMERA_INDEX = 0

# --- CẤU HÌNH GPIO ---
if LOCAL_MODE:
    import RPi.GPIO as GPIO
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
    # MOCK MODE (Render)
    RELAY_PINS = {0: {}, 1: {}, 2: {}}
    SENSOR_PINS = {0: 0, 1: 1, 2: 2}


# --- BIẾN TRẠNG THÁI ---
system_state = {
    "status": "Online",
    "lanes": [
        {"name": "Hộp Sữa", "status": "Sẵn sàng", "count": 0, "sensor": 1, "relay_grab": 1, "relay_push": 0},
        {"name": "Kim Loại", "status": "Sẵn sàng", "count": 0, "sensor": 1, "relay_grab": 1, "relay_push": 0},
        {"name": "Nhựa", "status": "Sẵn sàng", "count": 0, "sensor": 1, "relay_grab": 1, "relay_push": 0}
    ]
}
state_lock = threading.Lock()
output_frame = None
frame_lock = threading.Lock()
connected_clients = set()


# --- BROADCAST ---
def broadcast(message):
    json_message = json.dumps(message)
    for client in list(connected_clients):
        try:
            client.send(json_message)
        except Exception:
            connected_clients.remove(client)


def update_and_broadcast_state():
    with state_lock:
        broadcast({"type": "state_update", "state": system_state})


# --- CẢM BIẾN GIÁM SÁT ---
def sensor_monitor_thread():
    previous_states = [-1, -1, -1]
    while True:
        for i in range(3):
            if LOCAL_MODE:
                current_state = GPIO.input(SENSOR_PINS[i])
            else:
                # Giả lập cảm biến random
                current_state = random.choice([0, 1])

            if current_state != previous_states[i]:
                previous_states[i] = current_state
                lane_name = system_state["lanes"][i]["name"]

                with state_lock:
                    system_state["lanes"][i]["sensor"] = current_state
                broadcast({"type": "state_update", "state": system_state})
                broadcast({
                    "type": "log",
                    "log_type": "sensor",
                    "name": lane_name,
                    "status": current_state,
                    "timestamp": time.strftime("%H:%M:%S")
                })
        time.sleep(0.5)


# --- PHÂN LOẠI VẬT ---
def real_sorting_process(lane_index):
    print(f"[GPIO] Chu trình phân loại cho lane {lane_index}")

    if not LOCAL_MODE:
        time.sleep(1)
        with state_lock:
            system_state["lanes"][lane_index]["count"] += 1
        broadcast({
            "type": "log",
            "log_type": "sort",
            "name": system_state["lanes"][lane_index]["name"],
            "count": system_state["lanes"][lane_index]["count"],
            "timestamp": time.strftime("%H:%M:%S")
        })
        return

    grab_pin = RELAY_PINS[lane_index]['grab']
    push_pin = RELAY_PINS[lane_index]['push']

    try:
        GPIO.output(grab_pin, GPIO.LOW)
        GPIO.output(push_pin, GPIO.HIGH)
        time.sleep(0.5)
        GPIO.output(push_pin, GPIO.LOW)
        GPIO.output(grab_pin, GPIO.HIGH)
    except Exception as e:
        print(f"[GPIO ERROR] {e}")

    with state_lock:
        system_state["lanes"][lane_index]["count"] += 1
    update_and_broadcast_state()


# --- QR SCANNER ---
def qr_scanner_thread():
    global output_frame
    if not LOCAL_MODE:
        return  # không chạy trên Render

    camera = cv2.VideoCapture(CAMERA_INDEX)
    detector = cv2.QRCodeDetector()
    last_sent_data = ""

    while True:
        ret, frame = camera.read()
        if not ret:
            continue
        data, bbox, _ = detector.detectAndDecode(frame)
        if data and data != last_sent_data:
            print("QR:", data)
            lane_index = -1
            if data.lower() == "milk_box":
                lane_index = 0
            elif data.lower() == "metal":
                lane_index = 1
            elif data.lower() == "plastic":
                lane_index = 2

            if lane_index >= 0:
                with state_lock:
                    system_state["lanes"][lane_index]["status"] = "Đang chờ vật..."
                broadcast({"type": "log", "log_type": "qr", "data": data, "timestamp": time.strftime("%H:%M:%S")})
                threading.Thread(target=real_sorting_process, args=(lane_index,), daemon=True).start()
            else:
                broadcast({"type": "log", "log_type": "unknown_qr", "data": data, "timestamp": time.strftime("%H:%M:%S")})

            last_sent_data = data
        with frame_lock:
            output_frame = frame.copy()


# --- FLASK + SOCKET ---
app = Flask(__name__)
sock = Sock(app)


@app.route('/')
def index():
    return render_template('index.html')


def generate_frames():
    global output_frame
    while True:
        with frame_lock:
            if output_frame is None:
                continue
            (flag, encoded_image) = cv2.imencode(".jpg", output_frame)
            if not flag:
                continue
        yield (b'--frame\r\n'
               b'Content-Type: image/jpeg\r\n\r\n' + bytearray(encoded_image) + b'\r\n')


@app.route('/video_feed')
def video_feed():
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
    try:
        threading.Thread(target=sensor_monitor_thread, daemon=True).start()
        threading.Thread(target=qr_scanner_thread, daemon=True).start()
        print("🔹 Flask server running at http://0.0.0.0:5000")
        app.run(host='0.0.0.0', port=5000)
    finally:
        if LOCAL_MODE:
            GPIO.cleanup()
