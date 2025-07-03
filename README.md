# CoolerApp 冷卻機監控與智慧化控制套件

> **雙核心：桌面監控 GUI ＋ Streamlit 聲控／聊天助理**
>
> * **`cooler_app.py`**：PyQt5 打造的冷卻機 Modbus 監控與資料擷取介面。
> * **`voice_app2.py`**：Streamlit 與 LangChain／Ollama 整合的智慧化操作介面，支援文字與語音輸入、AI 參數最佳化、NC‑Code 解析，自動透過 Socket 與 `cooler_app.py` 串接完成調溫。

---

## 1. 功能總覽

| 模組                 | 主要功能                                                                                                                                                                                                                                                                                   | 特色                                                                                        |
| ------------------ | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ----------------------------------------------------------------------------------------- |
| **cooler\_app.py** | • Modbus‑TCP 連線<br>• 每秒讀取液態／參考／設定溫度<br>• 手動寫入設定溫度<br>• 自動寫入 SQLite (`temperature_log.db`)<br>• 內建 `localhost:9999` Socket Server                                                                                                                                                       | - PyQt5 直覺化按鈕 & 數值顯示<br>- 全程 Logging (`cooler_app.log`)<br>- 跨平台（Win / Linux）             |
| **voice\_app2.py** | • Streamlit 深色儀表板 UI<br>• 語音 / 文字輸入切換<br>• NC‑Code ⬆️ 上傳 → 解析 RPM 與加工時長<br>• `find_optimal_temp_offset()` 以 Optuna + 兩組迴歸模型自動尋優<br>• `fetch_cooler_temperature()` 讀取指定時點溫度<br>• LangChain Function‑Calling + Ollama Llama3.2 回答領域知識<br>• 一鍵送出最佳 Offset → Socket `[TempOffset]:<value>` | - 具備 TTS (gTTS) 與語音辨識 (speech\_recognition)<br>- 介面即時 Chat Log 與自動重繪<br>- 前端 CSS 客製化、深色主題 |

---

## 2. 環境安裝

> 建議使用 **Python ≥3.10**，並建立虛擬環境。

```bash
# 建立虛擬環境（可自行替換 venv 名稱）
python -m venv venv
source venv/bin/activate  # Windows: venv\Scripts\activate

# 安裝主要套件
pip install -r requirements.txt
```

`requirements.txt` 建議內容（節錄）：

```
PyQt5>=5.15
pyModbusTCP>=0.2.0
streamlit>=1.35
gTTS
playsound==1.3.0   # 2.x 在 Linux 可能有問題
speechrecognition
optuna
joblib
pandas
langchain
langchain-ollama
langchain-experimental
ollama   # 本地模型推論服務
```

> **Mac 使用者注意**：`playsound` 可能需改用 `pyobjc` 版本；或自行以 `afplay` 播放 mp3。

---

## 3. 快速啟動

```bash
# ① 啟動冷卻機監控 GUI（Modbus 連線 & Socket Server）
python cooler_app.py

# ② 另開終端：啟動智慧化介面（預設瀏覽器埠 http://localhost:8501）
streamlit run voice_app2.py
```

> 第一次執行 `voice_app2.py` 會載入／建立 `temperature_log.db` 及 \*.joblib 能耗／誤差模型，可依需放置於同一目錄。

---

## 4. 系統架構圖

```
┌────────────┐        Socket (TCP:9999)        ┌──────────────────┐
│ cooler_app │◀───────────────────────────────▶│ voice_app2 (UI) │
│  (PyQt5)   │        [TempOffset]: ΔT        │  Streamlit      │
└────────────┘                                  │  + LangChain    │
     ▲  ▲  ▲                                     └──────────────────┘
     │  │  │              SQLite                ▲
     │  │  └────────────────────────────────────┘
     │  │                                    讀寫 `temperature_log.db`
     │  └── Modbus‑TCP  (502) 與實體冷卻機溝通
     └─ Logging
```

---

## 5. Socket 協議

| 指令格式                    | 說明                                                                                    |
| ----------------------- | ------------------------------------------------------------------------------------- |
| `[TempOffset]: <float>` | 將 `<float>` °C 偏差寫入冷卻機。`cooler_app.py` 收到後以 `write_single_register()` 實際下發。成功回覆 `OK`。 |

> 如需外部腳本快速測試：
>
> ```python
> import socket
> s = socket.socket(); s.connect(("localhost", 9999))
> s.send(b"[TempOffset]: 5.0"); print(s.recv(1024)); s.close()
> ```

---

## 6. 資料庫結構 `temperature_log.db`

```sql
CREATE TABLE temperature_log (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp       TEXT NOT NULL,
    sensor_liquid   REAL,
    sensor_reference REAL,
    set_temperature REAL
);
```

* `cooler_app.py` 每秒將最新讀值寫入。
* `voice_app2.py` 可根據時間差 (`delta_seconds` / `delta_minutes`) 查詢歷史紀錄。

---

## 7. AI 最佳化流程

1. `parse_nc_code_file()` 讀取上傳的 NC 程式，統計各 RPM 的累積運轉時長 (hr)。
2. 對每組 (RPM, Hour) 呼叫 `find_optimal_temp_offset()`：

   * 兩個 `joblib` 模型分別預測能耗 (Cooler/Machine) 與熱誤差 (Avg / Max)。
   * 動態權重成本函式平衡精度與能耗 → Optuna TPE 搜尋 2.5–8.5 °C 最佳 Offset。
3. 使用者可於 UI 中一鍵送出所有 Offset，或在 AI 建議後回答 **yes/no** 決定是否執行。

---

## 8. FAQ

<details>
<summary>啟動 `cooler_app.py` 時顯示「連線失敗」？</summary>

* 確認冷卻機 IP 與埠號 (預設 502) 是否正確。
* 工控網段請關閉 Windows 防火牆或加入例外。

</details>

<details>
<summary>Streamlit 畫面卡住或語音辨識無反應？</summary>

* 確認瀏覽器允許麥克風權限。
* Linux 若無法播放語音，請更換 `playsound==1.3.0` 或改用 `ffplay` 播放。

</details>

---

## 9. 安裝 Ollama 與下載 Llama3.2

Ollama 是本專案執行 LLM（於 `voice_app2.py` 透過 LangChain‑Ollama 呼叫）的本地推論伺服器。以下說明如何安裝與拉取模型。

### 9.1 安裝 Ollama

| 作業系統                                                                      | 步驟         |
| ------------------------------------------------------------------------- | ---------- |
| **Ubuntu / Debian**                                                       | \`\`\`bash |
| curl -fsSL [https://ollama.com/install.sh](https://ollama.com/install.sh) | sh         |
| ollama serve &   # 11434 埠，背景啟動                                           |            |

````|
| **macOS (Homebrew)** | ```bash
brew install ollama
brew services start ollama
``` |
| **Windows 10/11** | 1. 前往 <https://ollama.com/download> 下載 MSI 安裝程式。<br>2. 完成安裝後，Ollama Desktop 會在背景啟動 `ollama serve`（預設 11434 埠）。 |

> 如需自訂連線位置，設定環境變數：
> ```bash
> export OLLAMA_HOST=http://<ip>:11434  # Windows: setx OLLAMA_HOST "http://<ip>:11434"
> ```
> `voice_app2.py` 會自動讀取該變數。

### 9.2 下載模型 Llama3.2:latest

```bash
# ≈6‑8 GB，視量化/量化版本而定
ollama pull llama3.2:latest
````

> 完成後即可在執行 `streamlit run voice_app2.py` 時看到模型被載入並用於 Function‑Calling。

---

## 10. TODO

* [ ] 支援多機台冷卻機分群管理。
* [ ] 加入 Grafana／TimescaleDB，長期趨勢監控。
* [ ] 將語音 TTS 改用 Edge‑TTS 以減少 gTTS API 限制。

---

Made with ❤️  for 智慧製造研究.
