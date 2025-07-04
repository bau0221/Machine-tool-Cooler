# CoolerApp 冷卻機監控與智慧化控制套件

**雙核心：桌面監控 GUI ＋ Streamlit 聲控／聊天助理**

* **cooler\_app.py**：PyQt5 打造的冷卻機 Modbus 監控與資料擷取介面
* **voice\_app2.py**：Streamlit + LangChain/Ollama 整合的智慧化操作介面，支援文字與語音輸入、AI 參數優化及 NC‑Code 自動解析，經由 Socket 與 `cooler_app.py` 串接並完成調溫

---

## 目錄

1. [專案概覽](#專案概覽)
2. [功能總覽](#功能總覽)
3. [安裝與環境設定](#安裝與環境設定)
4. [快速啟動](#快速啟動)
5. [系統架構](#系統架構)
6. [資料庫結構](#資料庫結構)
7. [Socket 通訊協議](#socket-通訊協議)
8. [AI 最佳化流程](#ai-最佳化流程)
9. [FAQ](#faq)
10. [進階設定：Ollama 與 SQLite](#進階設定ollama-與-sqlite)
11. [TODO 清單](#todo-清單)

---

## 專案概覽

本專案包含兩個相互協作的介面：

1. **cooler\_app.py**（桌面 GUI）

   * 建立 Modbus‑TCP (502) 連線，讀取並顯示實時溫度
   * 手動／自動設定冷卻溫度並寫入機台
   * 每秒記錄資料至 SQLite (`temperature_log.db`)
   * 完整日誌記錄（`cooler_app.log`）

2. **voice\_app2.py**（Web 聲控／文字介面）

   * Streamlit 深色儀表板，支援文字與語音輸入
   * 上傳 NC‑Code，解析 RPM 與累積運行時間
   * 調用 Optuna + 多輸出迴歸模型自動搜尋最優 ΔT
   * LangChain Function‑Calling + Ollama Llama3.2 提供參數建議與工藝知識
   * 一鍵發送最佳 ΔT 設定至 `cooler_app.py`

---

## 功能總覽

| 模組             | 功能摘要                                                             |
| -------------- | ---------------------------------------------------------------- |
| cooler\_app.py | Modbus‑TCP 連線、溫度顯示、設定溫度、SQLite 記錄、Socket Server (localhost:9999) |
| voice\_app2.py | 語音/文字切換、NC‑Code 解析、AI 最佳化、LLM 語意互動、Socket Client                 |

特色：

* TTS / 語音辨識（gTTS, speech\_recognition）
* 即時 Chat Log 與介面重繪
* 深色主題與自訂 CSS

---

## 安裝與環境設定

> 建議使用 Python ≥ 3.10，並在虛擬環境中安裝

```bash
python -m venv venv
# macOS/Linux
source venv/bin/activate
# Windows
venv\Scripts\activate
pip install -r requirements.txt
```

**建議套件列表（節錄）**：

```
PyQt5>=5.15
pyModbusTCP>=0.2.0
streamlit>=1.35
gTTS
playsound==1.3.0
speechrecognition
optuna
joblib
pandas
langchain
langchain-ollama
ollama
```

> **macOS users**: 若 `playsound` 有問題，可改用 `afplay` 或 `pyobjc`。

---

## 快速啟動

1. 啟動桌面 GUI：

   ```bash
   python cooler_app.py
   ```
2. 開新終端，啟動 Web 智慧介面：

   ```bash
   streamlit run voice_app2.py
   ```
3. 首次執行會自動建立 `temperature_log.db` 與模型檔 (`*.joblib`)，請放在相同目錄。

---

## 系統架構

```
┌────────────┐      Socket (TCP:9999)      ┌──────────────────┐
│ cooler_app │<-------------------------->│ voice_app2 (UI) │
│  (PyQt5)   │     [TempOffset]: ΔT       │  Streamlit      │
└────────────┘                            │  + LangChain    │
     ▲  ▲  ▲                              └──────────────────┘
     │  │  └── Modbus‑TCP (502) 與冷卻機通訊
     │  └── 日誌記錄
     └── SQLite 資料庫 (temperature_log.db)
```

---

## 資料庫結構

```sql
CREATE TABLE temperature_log (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp        TEXT    NOT NULL,
    sensor_liquid    REAL,
    sensor_reference REAL,
    set_temperature  REAL
);
```

* `cooler_app.py` 每秒寫入最新讀值
* `voice_app2.py` 支援時間範圍查詢（秒／分）

---

## Socket 通訊協議

* **指令**：`[TempOffset]: <float>` → 設定冷卻偏差 (°C)
* **回應**：`OK` 表示已下發至機台

**範例**：

```python
import socket
s = socket.socket()
s.connect(('localhost', 9991))  # 9991 為 voice_app2 client port
s.send(b'[TempOffset]: 5.0')
print(s.recv(1024))
s.close()
```

---

## AI 最佳化流程

1. 解析 NC‑Code，累計各 RPM 運行時長
2. 呼叫 `find_optimal_temp_offset()`：

   * 兩組模型預測 Cooler/Machine 能耗與平均/最大熱誤差
   * 動態權重成本函式平衡精度與能耗
   * Optuna TPE 在 \[2.5, 8.5] °C 範圍搜尋最優 ΔT
3. 使用者可一鍵下發建議，或在 Chat 中輸入 yes/no 決定執行

---

## 訓練資料與模型流程

本專案以 **Cooling\_Machine\_Data\_EN.csv** 為樣本資料，欄位包含：

| 欄位             | 說明                 |
| -------------- | ------------------ |
| `RPM`          | 主軸轉速 (rev · min⁻¹) |
| `Hour`         | 累計加工時數 (h)         |
| `TempOffset`   | 冷卻機設定溫差 ΔT (°C)    |
| `CoolerPower`  | 冷卻機功率 (kW)         |
| `MachinePower` | 機台功率 (kW)          |
| `AvgError`     | 平均熱誤差 (µm)         |
| `MaxError`     | 最大熱誤差 (µm)         |

> 若新增感測器，可直接於 CSV 追加欄位，並在程式中擴充特徵。

### 1. 預處理

透過 `training_dataset.py` 的 `load_and_preprocess()` 函數：

1. 以 **Tab** 分隔讀取資料（預設 `	`）
2. 移除任何空值列，確保模型不受缺漏值影響

### 2. 特徵選擇與目標

```text
X = [RPM, Hour, TempOffset]
能耗目標  y_energy = [CoolerPower, MachinePower]
誤差目標  y_error  = [AvgError,  MaxError]
```

### 3. 模型架構

| 模型   | 多項式階數 | 正規化            | 演算法            | 目標                        |
| ---- | ----- | -------------- | -------------- | ------------------------- |
| 能耗模型 | 10    | StandardScaler | Ridge (α=0.03) | CoolerPower, MachinePower |
| 誤差模型 | 5     | StandardScaler | Ridge (α=0.1)  | AvgError, MaxError        |

使用 `MultiOutputRegressor` 以一次同時預測兩個輸出，並各自計算 MSE 作為評估指標。

### 4. 訓練與評估流程

```python
energy_model, error_model, X, y_energy, y_error = train_models(df)
energy_pred = energy_model.predict(X)
error_pred  = error_model.predict(X)
```

* 以 **全資料回填預測** 取得基準 MSE
* GPU/CPU 皆可執行，約數秒內完成

### 5. 模型持久化

```python
from joblib import dump

dump(energy_model, 'energy_model_poly_ridge.joblib')
dump(error_model,  'error_model_poly_ridge.joblib')
```

生成的 `.joblib` 檔會與 `voice_app2.py` 同目錄，自動載入供即時預測。

---

## FAQ

---

## 進階設定：Ollama 與 SQLite (Windows)

**1. 安裝 Ollama**

* **Windows 10/11**：

  1. 下載官方 MSI 安裝程式 ([https://ollama.com/download/windows](https://ollama.com/download/windows))
  2. 以系統管理員啟動，完成後重開機或執行 `services.msc` 確認 `Ollama` 服務運行
  3. PowerShell 驗證：

     ```powershell
     ollama --version
     ollama serve
     ```
  4. 成功後會顯示 "Ollama is running on [http://localhost:11434](http://localhost:11434)"

**2. 下載 Llama3.2**\*\*:latest\*\*

```bash
ollama pull llama3.2:latest
```

**3. 安裝 SQLite CLI（可選）**

1. 下載 Windows 二進位檔：[https://sqlite.org/download.html](https://sqlite.org/download.html)
2. 解壓至 `C:\sqlite`
3. 將路徑加入環境變數 PATH
4. 驗證：

   ```powershell
   sqlite3 --version
   ```
5. 使用：

   ```powershell
   sqlite3 temperature_log.db
   .tables
   SELECT COUNT(*) FROM temperature_log;
   .quit
   ```

---

## TODO 清單

*

---

*Made with ❤️ for 智慧製造研究*
