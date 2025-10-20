# -*- coding: utf-8 -*-
import cv2
import time
import json
import threading
import requests
import RPi.GPIO as GPIO
import base64
import os

# --- CẤU HÌNH ---
VPS_URL_BASE = "https://pi.kh4idev.id.vn" 
SECRET_TOKEN = "pi-secret-key" 
API_URLS = {"update": f"{VPS_URL_BASE}/update", "log": f"{VPS_URL_BASE}/log", "image": f"{VPS_URL_BASE}/image_update"}
REQUEST_HEADERS = {"X-Token": SECRET_TOKEN, "Content-Type": "application/json"}
CAMERA_INDEX = 0
SYNC_INTERVAL = 5
CONFIG_FILE = 'config.json'
STREAMING_FPS = 15 # Giữ FPS ở mức hợp lý
STREAM_RESOLUTION = (480, 360) # Độ phân giải mới để stream
JPEG_QUALITY = 50 # Giảm chất lượng ảnh để gửi nhanh hơn

# --- CẤU HÌNH GPIO ---
GPIO.setmode(GPIO.BOARD)
GPIO.setwarnings(False)
RELAY_PINS = { 
    0: {'push': 11, 'pull': 12},
    1: {'push': 13, 'pull': 8},
    2: {'push': 15, 'pull': 7}
}
SENSOR_PINS = { 0: 5, 1: 29, 2: 31 }

for pins in RELAY_PINS.values():
    GPIO.setup(pins['push'], GPIO.OUT)
    GPIO.setup(pins['pull'], GPIO.OUT)
for pin in SENSOR_PINS.values(): 
    GPIO.setup(pin, GPIO.IN, pull_up_down=GPIO.PUD_UP)

# --- BIẾN TRẠNG THÁI TRUNG TÂM ---
system_state = {
    "lanes": [
        {"name": "Loại 1", "status": "Sẵn sàng", "count": 0},
        {"name": "Loại 2", "status": "Sẵn sàng", "count": 0},
        {"name": "Loại 3", "status": "Sẵn sàng", "count": 0}
    ],
    "timing_config": {"cycle_delay": 0.3}
}
state_lock = threading.Lock()
main_loop_running = True
latest_frame = None
frame_lock = threading.Lock()

# --- HÀM LƯU/TẢI CẤU HÌNH CỤC BỘ ---
def load_local_config():
    global system_state
    default_delay = 0.3
    if os.path.exists(CONFIG_FILE):
        try:
            with open(CONFIG_FILE, 'r') as f:
                config = json.load(f)
                delay = config.get('timing_config', {}).get('cycle_delay', default_delay)
                with state_lock:
                    system_state['timing_config']['cycle_delay'] = delay
                print(f"Loaded local config: cycle_delay = {delay}")
        except (json.JSONDecodeError, IOError):
            print(f"Error reading {CONFIG_FILE}, using default {default_delay}s.")
            with state_lock: system_state['timing_config']['cycle_delay'] = default_delay
    else:
        print(f"{CONFIG_FILE} not found, using default {default_delay}s.")
        with state_lock: system_state['timing_config']['cycle_delay'] = default_delay

# --- HÀM TIỆN ÍCH & ĐIỀU KHIỂN ---
def reset_all_relays_to_default():
    print("[GPIO] Resetting all relays to default state (PULL ON).")
    for lane_pins in RELAY_PINS.values():
        GPIO.output(lane_pins['push'], GPIO.HIGH) # Push OFF (Active Low)
        GPIO.output(lane_pins['pull'], GPIO.LOW)  # Pull ON (Active Low)

def send_request(url_key, data):
    try:
        requests.post(API_URLS[url_key], json=data, headers=REQUEST_HEADERS, timeout=1.5, verify=True)
    except requests.exceptions.RequestException as e:
        print(f"[WARN] Request to {url_key} failed: {e}")

def send_snapshot(frame, qr_data=""):
    # Tối ưu hóa: Thu nhỏ ảnh trước khi gửi
    small_frame = cv2.resize(frame, STREAM_RESOLUTION)
    
    if qr_data:
        cv2.putText(small_frame, f"QR: {qr_data}", (10, 20), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 255, 0), 2, cv2.LINE_AA)
    
    _, buffer = cv2.imencode('.jpg', small_frame, [cv2.IMWRITE_JPEG_QUALITY, JPEG_QUALITY]) 
    b64_string = base64.b64encode(buffer).decode('utf-8')
    send_request("image", {"image": b64_string})

# --- CÁC LUỒNG XỬ LÝ SONG SONG ---
def camera_capture_thread():
    """Luồng này chỉ có nhiệm vụ chụp ảnh liên tục từ camera."""
    global latest_frame
    camera = cv2.VideoCapture(CAMERA_INDEX)
    if not camera.isOpened():
        print("[ERROR] Cannot open camera.")
        return

    while main_loop_running:
        ret, frame = camera.read()
        if ret:
            with frame_lock:
                latest_frame = frame.copy()
        time.sleep(1/30) # Chụp ở ~30 FPS
    camera.release()
    print("Camera capture thread stopped.")

def video_streaming_thread():
    """Luồng này gửi ảnh mới nhất lên server theo FPS đã định."""
    while main_loop_running:
        frame_to_stream = None
        with frame_lock:
            if latest_frame is not None:
                frame_to_stream = latest_frame.copy()
        
        if frame_to_stream is not None:
            # Tối ưu hóa: Gửi trực tiếp thay vì tạo thread mới cho mỗi frame
            send_snapshot(frame_to_stream, "")
        
        time.sleep(1 / STREAMING_FPS)
    print("Video streaming thread stopped.")

def sync_to_vps_thread():
    while main_loop_running:
        with state_lock:
            full_state = {
                "lanes": [
                    {**system_state['lanes'][0], "sensor": GPIO.input(SENSOR_PINS[0]), "relay_grab": 1 if GPIO.input(RELAY_PINS[0]['pull']) == GPIO.LOW else 0, "relay_push": 1 if GPIO.input(RELAY_PINS[0]['push']) == GPIO.LOW else 0},
                    {**system_state['lanes'][1], "sensor": GPIO.input(SENSOR_PINS[1]), "relay_grab": 1 if GPIO.input(RELAY_PINS[1]['pull']) == GPIO.LOW else 0, "relay_push": 1 if GPIO.input(RELAY_PINS[1]['push']) == GPIO.LOW else 0},
                    {**system_state['lanes'][2], "sensor": GPIO.input(SENSOR_PINS[2]), "relay_grab": 1 if GPIO.input(RELAY_PINS[2]['pull']) == GPIO.LOW else 0, "relay_push": 1 if GPIO.input(RELAY_PINS[2]['push']) == GPIO.LOW else 0},
                ],
                "timing_config": system_state['timing_config']
            }
        send_request("update", full_state)
        time.sleep(SYNC_INTERVAL)

def sorting_process(lane_index):
    if system_state["lanes"][lane_index]["status"] not in ["Sẵn sàng", "Đang chờ vật..."]: return
    
    with state_lock:
        delay = system_state['timing_config']['cycle_delay']
        log_name = system_state['lanes'][lane_index]['name']
        system_state["lanes"][lane_index]["status"] = "Đang phân loại..."

    print(f"[CYCLE] Starting for {log_name} with cycle delay: {delay}s")
    try:
        pull_pin, push_pin = RELAY_PINS[lane_index]['pull'], RELAY_PINS[lane_index]['push']
        GPIO.output(pull_pin, GPIO.HIGH)
        time.sleep(0.2)
        GPIO.output(push_pin, GPIO.LOW)
        time.sleep(delay)
        GPIO.output(push_pin, GPIO.HIGH)
        time.sleep(0.2)
        GPIO.output(pull_pin, GPIO.LOW)
    finally:
        with state_lock:
            lane_info = system_state["lanes"][lane_index]
            lane_info["status"] = "Sẵn sàng"
            lane_info["count"] += 1
            send_request("log", {"log_type": "sort", "name": log_name, "count": lane_info['count']})
    print(f"[CYCLE] Finished for {log_name}.")

def qr_detection_loop():
    """Luồng này chỉ xử lý việc nhận dạng QR từ ảnh mới nhất."""
    detector = cv2.QRCodeDetector()
    last_qr_data, last_qr_time = "", 0
    LANE_MAP = {"LOAI1": 0, "LOAI2": 1, "LOAI3": 2}
    
    while main_loop_running:
        current_frame = None
        with frame_lock:
            if latest_frame is not None:
                current_frame = latest_frame.copy()
        
        if current_frame is None:
            time.sleep(0.2) # Chờ có ảnh
            continue

        try:
            data, _, _ = detector.detectAndDecode(current_frame)
        except cv2.error as e:
            print(f"[QR ERROR] OpenCV error: {e}. Skipping frame.")
            data = None 
            time.sleep(0.25)
            continue
        
        if data and (data != last_qr_data or time.time() - last_qr_time > 3):
            last_qr_data, last_qr_time = data, time.time()
            data_upper = data.upper().strip()

            if data_upper in LANE_MAP:
                lane_index = LANE_MAP[data_upper]
                if system_state["lanes"][lane_index]["status"] == "Sẵn sàng":
                    send_request("log", {"log_type": "qr", "data": data_upper})
                    # Gửi ảnh có đánh dấu QR khi phát hiện
                    threading.Thread(target=send_snapshot, args=(current_frame.copy(), data_upper), daemon=True).start()
                    with state_lock: system_state["lanes"][lane_index]["status"] = "Đang chờ vật..."
                    timeout = time.time() + 15
                    while time.time() < timeout:
                        if GPIO.input(SENSOR_PINS[lane_index]) == 0:
                            threading.Thread(target=sorting_process, args=(lane_index,), daemon=True).start()
                            break
                        time.sleep(0.05)
                    else:
                        with state_lock: system_state["lanes"][lane_index]["status"] = "Sẵn sàng"
            elif data_upper == "NG": send_request("log", {"log_type": "ng_product", "data": data_upper})
            else: send_request("log", {"log_type": "unknown_qr", "data": data_upper})
        
        time.sleep(0.25) # Giảm tần suất quét QR để tiết kiệm CPU

# --- MAIN ---
if __name__ == "__main__":
    try:
        load_local_config()
        reset_all_relays_to_default()

        # Khởi động các luồng xử lý song song
        threading.Thread(target=sync_to_vps_thread, daemon=True).start()
        threading.Thread(target=camera_capture_thread, daemon=True).start()
        
        print("Waiting for first camera frame...")
        time.sleep(2) 
        
        threading.Thread(target=video_streaming_thread, daemon=True).start()
        
        print("Starting main QR detection loop...")
        qr_detection_loop()

    except KeyboardInterrupt: 
        print("\n🛑 Shutting down...")
    finally: 
        main_loop_running = False
        time.sleep(0.5) 
        GPIO.cleanup()
        print("GPIO cleaned up.")

