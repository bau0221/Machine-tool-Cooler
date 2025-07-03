import sys
import socket
import threading
import os
from PyQt5.QtWidgets import (
    QApplication, QWidget, QPushButton, QLabel, QVBoxLayout,
    QHBoxLayout, QLineEdit, QGroupBox, QGridLayout
)
import sqlite3
import datetime
from PyQt5.QtCore import QTimer
from pyModbusTCP.client import ModbusClient
from threading import Lock
import logging

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler('cooler_app.log')
    ]
)

class CoolerApp(QWidget):
    def __init__(self):
        super().__init__()
        self.modbus_client = ModbusClient()
        self.modbus_lock = Lock()
        self.read_temp_timer = QTimer()
        self.read_temp_timer.timeout.connect(self.read_temperature)
        self.init_db()  # 初始化資料庫
        self.initUI()
        self.start_socket_server(host='localhost', port=9999)

    def init_db(self):
        """建立資料庫和資料表（如果尚未存在的話）"""
        try:
            db_path = 'temperature_log.db'
            abs_path = os.path.abspath(db_path)
            logging.info(f"資料庫路徑: {abs_path}")
            
            self.db_connection = sqlite3.connect(db_path)
            cursor = self.db_connection.cursor()
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS temperature_log (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    timestamp TEXT NOT NULL,
                    sensor_liquid REAL,
                    sensor_reference REAL,
                    set_temperature REAL
                )
            ''')
            self.db_connection.commit()
            
            # 驗證資料表創建成功
            cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='temperature_log'")
            if cursor.fetchone():
                logging.info("✅ 資料表已建立成功")
            else:
                logging.error("❌ 資料表未成功建立")
                
            logging.info("資料庫初始化成功")
        except Exception as e:
            logging.error(f"資料庫初始化失敗: {e}")

    def log_temperature(self, values):
        """將讀取到的溫度資料記錄到資料庫中"""
        try:
            timestamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            sensor_liquid = values[0] / 100.0
            sensor_reference = values[1] / 100.0
            set_temperature = values[2] / 10.0
            
            # 使用已存在的資料庫連接
            cursor = self.db_connection.cursor()
            
            # 打印數據，確認即將寫入
            logging.info(f"即將寫入數據: {timestamp}, 液態溫度: {sensor_liquid}, 參考溫度: {sensor_reference}, 設定溫度: {set_temperature}")

            cursor.execute("""
                INSERT INTO temperature_log (timestamp, sensor_liquid, sensor_reference, set_temperature)
                VALUES (?, ?, ?, ?)
            """, (timestamp, sensor_liquid, sensor_reference, set_temperature))

            self.db_connection.commit()  # 確保提交
            logging.info(f"✅ 成功提交數據到資料庫: {timestamp}")
            
            # 驗證數據是否寫入成功
            cursor.execute("SELECT * FROM temperature_log ORDER BY id DESC LIMIT 1")
            last_record = cursor.fetchone()
            logging.info(f"最後一筆記錄: {last_record}")
            
        except Exception as e:
            logging.error(f"❌ 資料庫記錄錯誤: {e}")

    def check_db_data(self):
        """檢查資料庫數據"""
        try:
            cursor = self.db_connection.cursor()
            cursor.execute("SELECT COUNT(*) FROM temperature_log")
            count = cursor.fetchone()[0]
            logging.info(f"資料庫中有 {count} 筆記錄")
            cursor.execute("SELECT * FROM temperature_log ORDER BY id ASC")
            
            if count > 0:
                cursor.execute("SELECT * FROM temperature_log ORDER BY id DESC LIMIT 5")
                records = cursor.fetchall()
                for record in records:
                    logging.info(f"記錄: {record}")
                    
            self.status_label.setText(f"資料庫中有 {count} 筆記錄")
                
        except Exception as e:
            logging.error(f"檢查資料庫錯誤: {e}")
            self.status_label.setText(f"檢查資料庫錯誤: {e}")

    def read_temperature(self):
        """讀取溫度並記錄到資料庫"""
        try:
            with self.modbus_lock:
                values = self.modbus_client.read_input_registers(0x0004, 3)
            
            logging.info(f"從 Modbus 讀取到的數值: {values}")
            
            if values:
                self.update_temperature_ui(values)
                self.log_temperature(values)  # 讀取後同時記錄資料庫
            else:
                logging.warning("Modbus 沒有返回數值")
                self.status_label.setText("無法讀取溫度數值")
        except Exception as e:
            logging.error(f"讀取溫度失敗: {e}")
            self.status_label.setText(f"讀取溫度失敗：{e}")

    def initUI(self):
        # 設定全局風格，讓介面更美觀
        self.setStyleSheet("""
            QWidget {
                background-color: #f0f0f0;
                font-family: Arial;
                font-size: 14px;
            }
            QPushButton {
                background-color: #4CAF50;
                color: white;
                border-radius: 5px;
                padding: 5px;
            }
            QPushButton:hover {
                background-color: #45a049;
            }
            QLineEdit {
                border: 1px solid #ccc;
                border-radius: 3px;
                padding: 3px;
                background-color: white;
            }
            QLabel {
                color: #333;
            }
            QGroupBox {
                border: 1px solid gray;
                border-radius: 5px;
                margin-top: 10px;
            }
            QGroupBox::title {
                subcontrol-origin: margin;
                left: 10px;
                padding: 0 3px 0 3px;
            }
        """)

        # Group 1：連線設定
        connectionGroup = QGroupBox("連線設定")
        connectionLayout = QGridLayout()
        self.ip_address_input = QLineEdit()
        self.ip_address_input.setPlaceholderText("輸入 IP 位址")
        self.connect_button = QPushButton("連線")
        self.connect_button.clicked.connect(self.connect_to_device)
        connectionLayout.addWidget(QLabel("IP 位址:"), 0, 0)
        connectionLayout.addWidget(self.ip_address_input, 0, 1)
        connectionLayout.addWidget(self.connect_button, 1, 0, 1, 2)
        connectionGroup.setLayout(connectionLayout)

        # Group 2：溫度寫入
        temperatureWriteGroup = QGroupBox("溫度設定")
        temperatureWriteLayout = QGridLayout()
        self.temperature_input = QLineEdit()
        self.temperature_input.setPlaceholderText("輸入目標溫度")
        self.write_temp_button = QPushButton("寫入溫度")
        self.write_temp_button.clicked.connect(self.write_temperature)
        temperatureWriteLayout.addWidget(QLabel("目標溫度:"), 0, 0)
        temperatureWriteLayout.addWidget(self.temperature_input, 0, 1)
        temperatureWriteLayout.addWidget(self.write_temp_button, 1, 0, 1, 2)
        temperatureWriteGroup.setLayout(temperatureWriteLayout)

        # Group 3：溫度讀取與狀態顯示
        temperatureReadGroup = QGroupBox("溫度讀取")
        temperatureReadLayout = QVBoxLayout()
        self.read_temp_button = QPushButton("開始/停止自動讀取溫度")
        self.read_temp_button.clicked.connect(self.toggle_temperature_reading)
        
        # 添加檢查資料庫按鈕
        self.check_db_button = QPushButton("檢查資料庫")
        self.check_db_button.clicked.connect(self.check_db_data)
        
        self.status_label = QLabel("狀態：未連線")
        self.temp_label = QLabel("液態溫度感測器：-- °C")
        self.temp_label2 = QLabel("參考溫度感測器：-- °C")
        self.temp_label3 = QLabel("設定溫度：-- °C")
        temperatureReadLayout.addWidget(self.read_temp_button)
        temperatureReadLayout.addWidget(self.check_db_button)  # 新增按鈕
        temperatureReadLayout.addWidget(self.status_label)
        temperatureReadLayout.addWidget(self.temp_label)
        temperatureReadLayout.addWidget(self.temp_label2)
        temperatureReadLayout.addWidget(self.temp_label3)
        temperatureReadGroup.setLayout(temperatureReadLayout)

        # 主版面佈局
        mainLayout = QVBoxLayout()
        mainLayout.addWidget(connectionGroup)
        mainLayout.addWidget(temperatureWriteGroup)
        mainLayout.addWidget(temperatureReadGroup)
        self.setLayout(mainLayout)
        self.setWindowTitle("Cooler App")
        self.resize(400, 350)  # 略微加大以適應新按鈕
        self.show()

    def connect_to_device(self):
        ip_address = self.ip_address_input.text()
        try:
            self.modbus_client = ModbusClient(host=ip_address, port=502, auto_open=False)
            if self.modbus_client.open():
                self.status_label.setText("已連線到冷卻機")
                logging.info(f"成功連線到冷卻機: {ip_address}")
            else:
                self.status_label.setText("連線失敗")
                logging.error(f"連線失敗: {ip_address}")
        except Exception as e:
            self.status_label.setText(f"連線失敗：{e}")
            logging.error(f"連線發生異常: {e}")

    def write_temperature(self):
        if not self.modbus_client.is_open:
            self.status_label.setText("尚未連線到冷卻機")
            return
        try:
            temperature_value = float(self.temperature_input.text())
            with self.modbus_lock:
                result = self.modbus_client.write_single_register(0x0001, int(temperature_value * 10))
            
            logging.info(f"寫入溫度結果: {result}, 值: {temperature_value}")
            self.status_label.setText("溫度寫入成功")
        except Exception as e:
            logging.error(f"溫度寫入失敗: {e}")
            self.status_label.setText(f"溫度寫入失敗：{e}")

    def external_write_temperature(self, temperature_value):
        logging.info(f"開始外部寫入溫度，數值: {temperature_value}")
        if not self.modbus_client.is_open:
            self.status_label.setText("尚未連線到冷卻機")
            logging.warning("寫入失敗：冷卻機未連線")
            return
        try:
            temperature_value = float(temperature_value)
            with self.modbus_lock:
                result = self.modbus_client.write_single_register(0x0001, int(temperature_value * 10))
            logging.info(f"寫入結果: {result}")
            self.status_label.setText("外部寫入溫度成功")
        except Exception as e:
            self.status_label.setText(f"外部寫入溫度失敗：{e}")
            logging.error(f"外部寫入溫度錯誤: {e}")

    def toggle_temperature_reading(self):
        if not self.modbus_client.is_open:
            self.status_label.setText("尚未連線到冷卻機")
            return
        if not self.read_temp_timer.isActive():
            self.read_temp_timer.start(1000)
            self.status_label.setText("開始自動讀取溫度")
            logging.info("開始自動讀取溫度")
        else:
            self.read_temp_timer.stop()
            self.status_label.setText("停止自動讀取溫度")
            logging.info("停止自動讀取溫度")

    def update_temperature_ui(self, values):
        if len(values) >= 3:
            liquid_temp = values[0] / 100.0
            ref_temp = values[1] / 100.0
            set_temp = values[2] / 10.0
            
            self.temp_label.setText(f"液態溫度感測器：{liquid_temp:.2f} °C")
            self.temp_label2.setText(f"參考溫度感測器：{ref_temp:.2f} °C")
            self.temp_label3.setText(f"設定溫度：{set_temp:.1f} °C")
            
            logging.info(f"更新UI - 液態溫度: {liquid_temp:.2f}°C, 參考溫度: {ref_temp:.2f}°C, 設定溫度: {set_temp:.1f}°C")

    def start_socket_server(self, host='localhost', port=9999):
        server_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            server_socket.bind((host, port))
            server_socket.listen(5)
            logging.info(f"Socket server running on {host}:{port}")
        except Exception as e:
            logging.error(f"Socket server 啟動失敗: {e}")
            return

        def handle_client(client_socket):
            try:
                data = client_socket.recv(1024)
                if not data:
                    client_socket.close()
                    return
                message = data.decode('utf-8').strip()
                logging.info(f"收到 socket 訊息: {message}")
                if message.startswith("[TempOffset]:"):
                    try:
                        offset_str = message.split(":", 1)[1].strip()
                        offset_value = float(offset_str)
                        self.external_write_temperature(offset_value)
                        client_socket.send("OK".encode('utf-8'))
                    except Exception as e:
                        error_msg = f"Error: {e}"
                        client_socket.send(error_msg.encode('utf-8'))
                else:
                    client_socket.send("Invalid command".encode('utf-8'))
            finally:
                client_socket.close()

        def server_loop():
            while True:
                try:
                    client_socket, addr = server_socket.accept()
                    logging.info(f"Accepted connection from {addr}")
                    client_thread = threading.Thread(target=handle_client, args=(client_socket,))
                    client_thread.daemon = True
                    client_thread.start()
                except Exception as e:
                    logging.error(f"Socket server error: {e}")

        server_thread = threading.Thread(target=server_loop)
        server_thread.daemon = True
        server_thread.start()

    def closeEvent(self, event):
        """當應用程式關閉時，確保資料庫連接也關閉"""
        try:
            if hasattr(self, 'db_connection'):
                self.db_connection.close()
                logging.info("資料庫連接已關閉")
        except Exception as e:
            logging.error(f"關閉資料庫時發生錯誤: {e}")
        event.accept()

if __name__ == '__main__':
    app = QApplication(sys.argv)
    cooler_app_instance = CoolerApp()
    sys.exit(app.exec_())
# 192.168.40.30 255.25.128