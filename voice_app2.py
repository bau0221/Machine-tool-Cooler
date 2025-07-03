import streamlit as st
import socket
import logging
import sqlite3
from joblib import load
from langchain_experimental.llms.ollama_functions import OllamaFunctions
from langchain_ollama import ChatOllama
from langchain.prompts import ChatPromptTemplate
from langchain.schema import SystemMessage

# 新增語音處理所需套件
import subprocess
from gtts import gTTS
from playsound import playsound
import os
import speech_recognition as sr
from datetime import datetime, timedelta
import re
from collections import defaultdict
import time
import threading

# 配置 logging
logging.basicConfig(level=logging.INFO,
                    format='%(asctime)s - %(levelname)s - %(message)s')


# -----------------------------
# 與資料庫或模型相關的函式（略，與原程式相同）
# -----------------------------
def fetch_cooler_temperature(delta_seconds: int = None, delta_minutes: int = None):
    """
    從資料庫中抓取最新溫度記錄。
    若給定 delta_seconds（以秒計）或 delta_minutes（以分鐘計），
    會抓取與當前時間前對應時間點最接近的記錄，
    並回傳目前時間、目標時間與該筆資料的時間。
    """
    try:
        # 取得目前時間並記錄
        now = datetime.now()
        current_time_str = now.strftime("%Y-%m-%d %H:%M:%S")
        logging.info(f"取得目前時間：{current_time_str}")

        # 建立資料庫連線
        conn = sqlite3.connect('temperature_log.db', check_same_thread=False)
        logging.info("成功建立資料庫連線")
        cursor = conn.cursor()

        # 決定要回溯的秒數
        if delta_minutes is not None:
            total_seconds = delta_minutes * 60
            target_time = now - timedelta(seconds=total_seconds)
            unit, amount = "分鐘", delta_minutes
        elif delta_seconds is not None:
            total_seconds = delta_seconds
            target_time = now - timedelta(seconds=total_seconds)
            unit, amount = "秒鐘", delta_seconds
        else:
            total_seconds = None

        target_info = ""
        if total_seconds is not None:
            target_time_str = target_time.strftime("%Y-%m-%d %H:%M:%S")
            logging.info(f"目標時間計算：{target_time_str} (當前時間減 {amount}{unit})")
            target_info = f"目標時間（{amount}{unit}前）：{target_time_str}\n"

            query = """
            SELECT *, ABS(strftime('%s', timestamp) - strftime('%s', ?)) AS diff
            FROM temperature_log
            ORDER BY diff ASC LIMIT 1;
            """
            logging.info(f"執行 SQL 查詢：{query.strip()}，參數：{target_time_str}")
            cursor.execute(query, (target_time_str,))
        else:
            query = "SELECT * FROM temperature_log ORDER BY id DESC LIMIT 1;"
            logging.info(f"執行 SQL 查詢：{query}")
            cursor.execute(query)

        row = cursor.fetchone()
        logging.info(f"取得查詢結果：{row}")
        conn.close()
        logging.info("關閉資料庫連線")

        if row:
            record_time = row[1]  # 假設第2個欄位為 timestamp
            result = (
                f"記錄總數: ID={row[0]}, 時間={row[1]}\n"
                f"目前時間：{current_time_str}\n"
                f"{target_info}"
                f"資料記錄時間：{record_time}\n"
                f"液態溫度={row[2]}°C, 參考溫度={row[3]}°C, 設定溫度={row[4]}°C"
            )
            logging.info("成功取得記錄並生成結果字串")
            return result
        else:
            logging.warning("查詢結果為空，資料庫中沒有記錄")
            return f"目前時間：{current_time_str}\n資料庫中沒有記錄."
    except Exception as e:
        logging.error(f"Database error: {e}")
        return f"資料庫錯誤: {e}"


from joblib import load

from pathlib import Path
import numpy as np
import pandas as pd
from joblib import load
import optuna

def find_optimal_temp_offset(
    rpm: float,
    hour: float,
    *,
    energy_model_path: str = "energy_model_poly_ridge.joblib",
    error_model_path: str  = "error_model_poly_ridge.joblib",
    offset_min: float = 2.5,
    offset_max: float = 8.5,
    offset_step: float = 0.1,
    n_trials: int = 60,
    seed: int = 42,
):
    """回傳 (explanation, best_offset)"""

    # ───────── 1. 參數常數 ─────────
    _MEDIAN_ABS_AVG_ERR = 6.922       # µm
    _MEDIAN_TOTAL_POWER = 4974.04     # W
    _MEDIAN_ABS_MAX_ERR = 11.059      # µm

    _W_AVG_BASE = 1 / _MEDIAN_ABS_AVG_ERR
    _W_PWR_BASE = 1 / _MEDIAN_TOTAL_POWER
    _W_MAX_BASE = 1 / _MEDIAN_ABS_MAX_ERR

    # ───────── 2. 動態權重 ─────────
    def weight_rules(rpm_val: float, hour_val: float):
        rpm_min, rpm_max = 1500, 12000
        rpm_norm  = max(0.0, min(1.0, (rpm_val - rpm_min) / (rpm_max - rpm_min)))
        hour_norm = max(0.0, min(1.0, hour_val / 8.0))

        k_avg, k_max, k_pow, damp = 2.0, 1.0, 3.0, 0.01
        w_avg = _W_AVG_BASE * (1 + k_avg * rpm_norm)
        w_max = _W_MAX_BASE * (1 + k_max * rpm_norm)
        w_pow = _W_PWR_BASE * (1 + k_pow * hour_norm) * (1 - damp * rpm_norm)
        return w_avg, w_pow, w_max

    # ───────── 3. 載入模型（快取） ─────────
    energy_model = load(Path(energy_model_path))
    error_model  = load(Path(error_model_path))

    # ───────── 4. 成本函式 ─────────
    def cost_fn(offset: float):
        X_df = pd.DataFrame([[rpm, hour, offset]],
                            columns=["RPM", "Hour", "TempOffset"])
        c_power, m_power         = energy_model.predict(X_df)[0]
        avg_err,  max_err        = error_model.predict(X_df)[0]
        w_avg, w_pow, w_max      = weight_rules(rpm, hour)
        total_power              = c_power + m_power
        cost = (w_avg * abs(avg_err) +
                w_pow * total_power +
                w_max * abs(max_err))
        return cost

    # ───────── 5. Optuna 最佳化 ─────────
    sampler = optuna.samplers.TPESampler(seed=seed)
    study   = optuna.create_study(direction="minimize", sampler=sampler)

    def objective(trial):
        offset = trial.suggest_float(
            "temp_offset", offset_min, offset_max, step=offset_step
        )
        return cost_fn(offset)

    study.optimize(objective, n_trials=n_trials, show_progress_bar=False)
    best_offset = study.best_params["temp_offset"]

    # ───────── 6. 取得最佳預測值 ─────────
    X_best = pd.DataFrame([[rpm, hour, best_offset]],
                          columns=["RPM", "Hour", "TempOffset"])
    c, m   = energy_model.predict(X_best)[0]
    a_err, m_err = error_model.predict(X_best)[0]
    w_avg, w_pow, w_max = weight_rules(rpm, hour)

    # ───────── 7. 組裝說明 ─────────
    explanation = (
        f"在 RPM={rpm:.0f}, Hour={hour:.2f} 小時 的情境下，\n"
        f"採用加權總分評估 (w_avg_err={w_avg:.5f}、w_power={w_pow:.6f}、w_max_err={w_max:.5f})，\n"
        f"最佳 TempOffset = {best_offset:.1f} °C。\n"
        f"預測 CoolerPower = {c:.2f} W，MachinePower = {m:.2f} W，"
        f"總能耗 = {c + m:.2f} W。\n"
        f"預測 AvgError = {a_err:.2f} μm，MaxError = {m_err:.2f} μm。\n"
    )

    return explanation, best_offset
# -----------------------------
# 新增：解析上傳 NC code 檔案內容
# -----------------------------
def parse_nc_code_file(uploaded_file):
    """
    從上傳的 txt 檔案讀取 NC code，
    解析出每一行中的 RPM (Sxxxx) 與延遲時間指令 (G04 Fxxxx.)
    將延遲時間（秒）轉換成小時後累計，回傳字典 (key: RPM, value: 小時數)
    """
    rpm_durations = defaultdict(float)
    current_rpm = None
    # 為確保檔案指標在開頭，重新定位
    uploaded_file.seek(0)
    content = uploaded_file.read().decode('utf-8')
    lines = content.splitlines()

    for line in lines:
        line = line.strip()
        # 解析 RPM：尋找 S後接數字，如 S1200
        rpm_match = re.search(r'\bS(\d+)', line, re.IGNORECASE)
        if rpm_match:
            current_rpm = int(rpm_match.group(1))
        # 解析延遲時間：尋找 G04 Fxxxx.，單位為秒，轉換成小時
        dwell_match = re.search(r'G04\s+F(\d+(?:\.\d+)?)', line, re.IGNORECASE)
        if dwell_match and current_rpm is not None:
            delay_seconds = float(dwell_match.group(1))
            delay_hours = delay_seconds / 3600.0
            rpm_durations[current_rpm] += delay_hours

    return rpm_durations
from langchain.prompts import (
    ChatPromptTemplate,
    SystemMessagePromptTemplate,
    HumanMessagePromptTemplate
)

# 先準備一個 prompt template，用於基礎知識取回後的追問
follow_up_prompt = ChatPromptTemplate.from_messages([
    SystemMessagePromptTemplate.from_template(
        "以下是基礎知識：\n\n{basics}\n\n"
        "請結合上述知識，針對使用者問題直接回答，**不要呼叫任何工具**。"
    ),
    HumanMessagePromptTemplate.from_template("{input}")
])


def get_cooling_machine_basics():
    """
    提供工具機用冷卻機的基本知識，包括工作原理、主要組件、操作參數與應用場景。
    """
    content = """
## 一、工作原理
工具機冷卻機（Chiller）主要透過閉迴路的冷凍循環或液體循環，將機床主軸或切削區域產生的熱量帶走並排放到環境中。常見的冷凍循環包含四大步驟：
1. **壓縮（Compression）**：壓縮機將低壓低溫的冷媒壓縮成高壓高溫的氣體。
2. **冷凝（Condensation）**：高溫氣體經由冷凝器放熱，凝結成高壓液態冷媒，同時將熱量釋放到空氣或水側。
3. **膨脹（Expansion）**：液態冷媒通過膨脹閥快速降壓、降溫，成為低溫低壓的液態或氣液混合物。
4. **蒸發（Evaporation）**：冷媒在蒸發器內吸熱，蒸發成氣體，帶走水／油／乳化液中的熱量，完成冷卻循環。

## 二、主要組件
- **壓縮機 (Compressor)**：產生冷凍循環所需的壓力差，可選擇活塞式、螺桿式或渦旋式。
- **冷凝器 (Condenser)**：以風冷或水冷方式，將壓縮後的高溫冷媒排熱；風冷體積小、安裝方便，水冷效率更高。
- **膨脹閥 (Expansion Valve)**：精確控制冷媒流量，維持蒸發器內適當的壓力與溫度。
- **蒸發器 (Evaporator)**：冷媒吸熱蒸發的場所，可分板式、殼管式或微通道式，直接與冷卻水或乳化液進行熱交換。
- **循環泵浦 (Pump)**：驅動冷卻液／冷媒在機床與冷卻機之間循環；流量與壓力需依機床規格選配。
- **儲液槽與過濾系統**：穩定液位、去除雜質，並在系統維護或循環故障時提供緩衝。
- **控制系統**：溫度感測器（PT100、熱電偶）＋ PID 控制器，負責保持冷卻液出口溫度在設定值附近。

## 三、主要操作參數
- **設定冷卻出口溫度**：典型範圍在 15–25 °C，視切削條件與機台規格而定。
- **流量 (Flow Rate)**：依加工熱量與管路損失，常見 5–20 L/min；保證各冷卻點均有足夠冷卻液。
- **壓力 (Pressure)**：一般維持 0.2–0.5 MPa，確保冷卻液能有效到達所有冷卻通道。
- **冷媒濃度或導熱油黏度**：若使用導熱油或乳化液，需定期檢測並調整濃度，以維持熱交換效能。
- **循環泵轉速**：可透過變頻驅動器 (VFD) 動態調整，達到節能與穩定溫控的平衡。

## 四、應用場景
1. **高速銑削與精密磨削**：主軸因高速運轉產生大量熱能，需穩定主軸內部軸承與錐度間隙。
2. **五軸複合加工**：多方向多開刀頭同步運轉，對冷卻均勻性與反應速度要求極高。
3. **線切割與 EDM**：雖不直接與切削液接觸，但切削液溫度變化仍會影響機台結構與精度。
4. **塑膠射出與模具冷卻**：模具溫度均勻性直接影響成形品質，需快速抽走模穴熱量。
"""
    return content

# -----------------------------
# 定義 AI 模型與工具設定（略，與原程式相同）
# -----------------------------
tools = [
    {
        "name": "find_optimal_temp_offset",
        "description": "根據 RPM 與運轉時間計算最佳的溫度偏差",
        "parameters": {
            "type": "object",
            "properties": {
                "rpm": {
                    "type": "integer",
                    "description": "主軸轉速"
                },
                "hour": {
                    "type": "integer",
                    "description": "運轉時間（小時）"
                }
            },
            "required": ["rpm", "hour"]
        }
    },
    {
        "name": "fetch_cooler_temperature",
        "description": "從冷卻系統中讀取目前的溫度資料或抓取指定相對時間之前的最新記錄",
        "parameters": {
            "type": "object",
            "properties": {
                "delta_seconds": {
                    "type": "integer",
                    "description": "距離當前時間的秒數，例如 40 表示取40秒前的資料。若不提供則回傳最新記錄。"
                }
            },
            "required": []
        }
    },
    {
        "name": "get_cooling_machine_basics",
        "description": "提供工具機用冷卻機的基本知識，包括工作原理、主要組件、操作參數與應用場景",
        "parameters": {
            "type": "object",
            "properties": {},
            "required": []
        }
    }
]

# 使用 OllamaFunctions 初始化 AI 模型
model = OllamaFunctions(model="llama3.2", format="json", temperature=0)
model = model.bind_tools(tools=tools)

plain_model = ChatOllama(model="llama3.2", temperature=0)


prompt = ChatPromptTemplate.from_messages([
    SystemMessage(content=( 
        "你是一個提供幫助的 AI 助手。"
        "如果使用者查詢涉及實際資料（例如冷卻系統溫度或冷卻系統最佳化），"
        "你**必須**呼叫相應的函式並直接回傳結果。"
        "請勿回傳 JSON，只需返回最終輸出結果。"
        "若使用者查詢與工具無關，則請一般回答。"
    )),
    ("human", "{input}")
])

def process_query(query):
    """根據使用者的查詢處理並回傳結果"""
    logging.info(f"Processing query: {query}")
    # 1. 先讓模型決定要不要呼叫工具
    formatted_prompt = prompt.format_messages(input=query)
    result = model.invoke(formatted_prompt)
    logging.info(f"Model result: {result}")

    # 如果有 tool_calls，處理它們
    if hasattr(result, "tool_calls") and result.tool_calls:
        for call in result.tool_calls:
            fn = call.get("name")
            args = call.get("args", {})
            logging.info(f"Function call detected: {fn} with args {args}")

            if fn == "fetch_cooler_temperature":
                delta = args.get("delta_seconds")
                return fetch_cooler_temperature(delta)

            elif fn == "find_optimal_temp_offset":
                explanation, best = find_optimal_temp_offset(**args)
                st.session_state.pending_offset = best
                return explanation + "\n請問是否需要自動調整？請回覆 'yes' 或 'no'."

            elif fn == "get_cooling_machine_basics":
                basics = get_cooling_machine_basics()
                follow_up_messages = follow_up_prompt.format_messages(
                    basics=basics,
                    input=query
                )
                follow_up_result = plain_model.invoke(follow_up_messages)
                # 這時模型應該直接回 content，不再 tool_calls
                return getattr(follow_up_result, "content", str(follow_up_result))


    # 沒有呼叫工具就把 LLM 原始回應的 content 回去
    if hasattr(result, "content"):
        return result.content
    elif isinstance(result, dict) and "content" in result:
        return result["content"]
    elif isinstance(result, str):
        return result
    else:
        return str(result)
# -----------------------------
# 新增語音輸入與語音輸出工具
# -----------------------------
def record_audio():
    """使用 speech_recognition 透過麥克風捕捉語音並轉換為文字"""
    recognizer = sr.Recognizer()
    mic = sr.Microphone()
    with mic as source:
        st.info("請開始說話...")
        audio = recognizer.listen(source, phrase_time_limit=5)  # 限制錄音時間
    try:
        text = recognizer.recognize_google(audio, language="zh-TW")
        st.write(f"辨識結果：{text}")
        return text
    except sr.UnknownValueError:
        st.error("無法辨識語音")
        return None
    except sr.RequestError as e:
        st.error(f"語音辨識服務錯誤: {e}")
        return None

def speak_text(text):
    """使用 gTTS 將文字轉換為語音並播放"""
    try:
        tts = gTTS(text=text, lang='zh')
        filename = "response.mp3"
        tts.save(filename)
        playsound(filename)
        os.remove(filename)
    except Exception as e:
        st.error(f"語音播放發生錯誤: {e}")


def send_offset(rpm, offset):
    """實際連 socket 傳送 offset，回傳伺服器回應或拋例外。"""
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.connect(('localhost', 9999))
    s.send(f"[TempOffset]: {offset}".encode('utf-8'))
    resp = s.recv(1024).decode('utf-8')
    s.close()
    return resp


# -----------------------------
# Streamlit 主程式 - 美化版（即時顯示修復）
# -----------------------------
def main():
    # 設置頁面配置和標題
    st.set_page_config(
        page_title="冷卻系統最佳化",
        page_icon="❄️",
        layout="wide"
    )
    
    # 添加自定義 CSS (保留原有CSS)
    st.markdown("""
        <style>
        /* 全局樣式 */
        body {
            font-family: 'Helvetica Neue', Arial, sans-serif;
            color: #e5e7eb;                     /* 淺灰文字 */
            background-color: #18181b;         /* 深色背景 */
        }
        
        /* 頁面標題樣式 */
        .main-title {
            color: #e5e7eb;
            font-size: 2.5rem;
            font-weight: 700;
            text-align: center;
            margin-bottom: 2rem;
            padding-bottom: 1rem;
            border-bottom: 3px solid #2563eb;  /* 藍色分隔線 */
        }
        
        /* 區段標題樣式 */
        .section-title {
            color: #e5e7eb;
            font-size: 1.5rem;
            font-weight: 600;
            margin-top: 1.5rem;
            margin-bottom: 1rem;
            padding-left: 0.5rem;
            border-left: 4px solid #2563eb;
        }
        
        /* 聊天容器樣式 */
        .chat-container {
            width: 100%;
            height: 500px;
            overflow-y: auto;
            border: 1px solid #374151;         /* 深灰邊框 */
            border-radius: 12px;
            padding: 1rem;
            background-color: #1f1f1f;         /* 容器深底 */
            box-shadow: 0 4px 6px rgba(0, 0, 0, 0.5);
        }
        
        /* 聊天訊息樣式 */
        .message {
            padding: 12px 16px;
            border-radius: 18px;
            margin: 12px 0;
            max-width: 75%;
            word-wrap: break-word;
            box-shadow: 0 1px 2px rgba(0, 0, 0, 0.5);
        }
        
        /* 使用者訊息樣式 */
        .user-message {
            background: linear-gradient(135deg, #2563eb, #1e40af);
            color: #f3f4f6;
            float: right;
            border-bottom-right-radius: 4px;
        }
        
        /* 系統訊息樣式 */
        .system-message {
            background-color: #252526;
            border: 1px solid #374151;
            color: #cbd5e1;
            float: left;
            border-bottom-left-radius: 4px;
        }
        
        /* 清除浮動 */
        .clear {
            clear: both;
        }
        
        /* 按鈕樣式 */
        .stButton > button {
            background-color: #2563eb;
            color: white;
            border-radius: 8px;
            border: none;
            padding: 0.5rem 1rem;
            font-weight: 600;
            transition: all 0.3s ease;
        }
        
        .stButton > button:hover {
            background-color: #1e40af;
            box-shadow: 0 4px 6px rgba(37, 99, 235, 0.4);
            transform: translateY(-2px);
        }
        
        /* 輸入框樣式 */
        .stTextInput > div > div > input {
            border-radius: 8px;
            border: 1px solid #374151;
            padding: 0.75rem;
            background-color: #252526;
            color: #e5e7eb;
        }
        
        /* 檔案上傳區域樣式 */
        .upload-area {
            background-color: #252526;
            border: 2px dashed #2563eb;
            border-radius: 12px;
            padding: 1.5rem;
            text-align: center;
            color: #9ca3af;
        }
        
        /* 卡片樣式 */
        .card {
            background-color: #1f1f1f;
            border-radius: 10px;
            padding: 1.5rem;
            box-shadow: 0 4px 6px rgba(0, 0, 0, 0.5);
            margin-bottom: 1rem;
        }
        
        /* 捲軸樣式 */
        .chat-container::-webkit-scrollbar {
            width: 6px;
        }
        
        .chat-container::-webkit-scrollbar-track {
            background: #2d2d2d;
            border-radius: 10px;
        }
        
        .chat-container::-webkit-scrollbar-thumb {
            background: #2563eb;
            border-radius: 10px;
        }
        
        .chat-container::-webkit-scrollbar-thumb:hover {
            background: #1e40af;
        }
        
        /* 成功訊息樣式 */
        .success-message {
            background-color: #1e293b;
            border-left: 4px solid #10b981;
            padding: 1rem;
            border-radius: 6px;
            margin: 1rem 0;
            color: #d1fae5;
        }
        
        /* 錯誤訊息樣式 */
        .error-message {
            background-color: #2b0505;
            border-left: 4px solid #ef4444;
            padding: 1rem;
            border-radius: 6px;
            margin: 1rem 0;
            color: #fee2e2;
        }
        
        /* 提示訊息樣式 */
        .info-message {
            background-color: #1e293b;
            border-left: 4px solid #2563eb;
            padding: 1rem;
            border-radius: 6px;
            margin: 1rem 0;
            color: #cbd5e1;
        }
        </style>
    """, unsafe_allow_html=True)

    # 標題
    st.markdown("<h1 class='main-title'>❄️ 冷卻系統最佳化聊天介面</h1>", unsafe_allow_html=True)

    # 初始化 session_state
    if "chat_history" not in st.session_state:
        st.session_state.chat_history = []
    if "pending_offset" not in st.session_state:
        st.session_state.pending_offset = None
    if "voice_mode" not in st.session_state:
        st.session_state.voice_mode = False
    if "needs_rerun" not in st.session_state:
        st.session_state.needs_rerun = False

    # 使用列（columns）來分割介面
    col1, col2 = st.columns([2, 3])

    # 左側欄位 - 控制面板
    with col1:
        st.markdown("<div class='card'>", unsafe_allow_html=True)
        st.markdown("<h2 class='section-title'>控制中心</h2>", unsafe_allow_html=True)
        
        # 模式切換按鈕（文字模式與語音模式）
        st.markdown("<div class='mode-toggle'>", unsafe_allow_html=True)
        next_mode = "文字模式 ⌨️" if st.session_state.voice_mode else "語音模式 🎤"
        if st.button(f"切換到 {next_mode}"):
            st.session_state.voice_mode = not st.session_state.voice_mode
            mode = "語音模式 🎤" if st.session_state.voice_mode else "文字模式 ⌨️"
            st.session_state.chat_history.append(("系統", f"已切換至 {mode}"))
            st.session_state.needs_rerun = True
        st.markdown("</div>", unsafe_allow_html=True)
        
        # NC code 上傳與解析
        st.markdown("<h2 class='section-title'>NC Code 上傳與解析</h2>", unsafe_allow_html=True)
        st.markdown("<div class='upload-area'>", unsafe_allow_html=True)
        uploaded_file = st.file_uploader("選擇 NC code 檔案", type=["txt"], key="nc_uploader")
        st.markdown("</div>", unsafe_allow_html=True)
        
        if uploaded_file is not None:
            if st.button("📊 讀取並分析 NC code", key="analyze_nc_button"):
                try:
                    rpm_durations = parse_nc_code_file(uploaded_file)
                    nc_parameters = {}
                    if rpm_durations:
                        analysis_result = "🔍 NC code 解析結果：\n"
                        for rpm, hours in rpm_durations.items():
                            analysis_result += f"\n⚙️ RPM: {rpm} => 運作時間: {hours:.2f} 小時"
                            explanation, best_offset = find_optimal_temp_offset(rpm, hours)
                            nc_parameters[rpm] = {
                                "hours": hours,
                                "best_offset": best_offset,
                                "explanation": explanation
                            }
                            analysis_result += f"\n🔧 最佳化結果：{explanation}\n"
                    else:
                        analysis_result = "❌ 未能解析出任何 RPM 與運作時間資訊。"
                    st.session_state.chat_history.append(("使用者", "分析NC-CODE中"))
                    st.session_state.chat_history.append(("系統", analysis_result))
                    st.session_state.nc_parameters = nc_parameters
                    st.session_state.needs_rerun = True
                except Exception as e:
                    st.session_state.chat_history.append(("系統", f"❌ 檔案解析發生錯誤：{e}"))
                    st.session_state.needs_rerun = True
        
    # 自動調整確認區塊
    nc_parameters = st.session_state.get("nc_parameters", {})

    if nc_parameters:
        st.markdown("### 📋 待調整轉速與參數", unsafe_allow_html=True)
        for rpm, p in nc_parameters.items():
            st.markdown(
                f"- **RPM {rpm}**：時長 {p['hours']:.2f}h，"
                f"Offset={p['best_offset']}，說明：{p['explanation']}"
            )
        st.markdown("---", unsafe_allow_html=True)

        # 初始化 state
        if "auto_started" not in st.session_state:
            st.session_state.auto_started = False
        if "log" not in st.session_state:
            st.session_state.log = []

        # 啟動開關
        start = st.radio("是否進行自動調整？", ("否", "是")) == "是"
        if start and not st.session_state.auto_started:
            st.session_state.auto_started = True

        # 真正的自動迴圈
        if st.session_state.auto_started:
            placeholder = st.empty()  # 留一個 container 即時更新 log

            for rpm, p in nc_parameters.items():
                # 呼叫 send_offset
                try:
                    print(rpm)
                    resp = send_offset(rpm, p["best_offset"])
                except Exception as e:
                    resp = f"❌ {e}"

                # 紀錄
                now = datetime.now().strftime("%H:%M:%S")
                st.session_state.log.append({
                    "time": now,
                    "rpm": rpm,
                    "offset": p["best_offset"],
                    "explanation": p["explanation"],
                    "resp": resp
                })

                # 更新 log 顯示
                placeholder.markdown("### ✅ 已完成調整\n" +
                    "\n".join(
                        f"- `{e['time']}` RPM {e['rpm']} → Offset={e['offset']}，說明：{e['explanation']}，回應：{e['resp']}"
                        for e in st.session_state.log
                    ),
                    unsafe_allow_html=True
                )
                print("Time",p['hours'])
                # 間隔 5 秒
                time.sleep(5)

            st.success("🎉 所有轉速的自動調整操作已完成。")
    # 右側欄位 - 聊天系統
    with col2:
        st.markdown("<div class='card'>", unsafe_allow_html=True)
        st.markdown("<h2 class='section-title'>系統對話</h2>", unsafe_allow_html=True)
        
        # 聊天記錄顯示區
        chat_html = "<div class='chat-container'>"
        for role, message in st.session_state.chat_history:
            if role == "使用者":
                chat_html += f"<div class='message user-message'>{message}</div><div class='clear'></div>"
            else:
                chat_html += f"<div class='message system-message'>{message}</div><div class='clear'></div>"
        chat_html += "</div>"
        st.markdown(chat_html, unsafe_allow_html=True)
        
        # 根據是否為語音模式決定輸入方式
        if st.session_state.voice_mode:
            st.markdown("<h3 class='section-title'>🎤 語音輸入模式</h3>", unsafe_allow_html=True)
            if st.button("🎙️ 開始錄音"):
                user_text = record_audio()
                if user_text:
                    st.session_state.chat_history.append(("使用者", user_text))
                    response = process_query(user_text)
                    st.session_state.chat_history.append(("系統", response))
                    st.session_state.needs_rerun = True  # 標記需要重新執行
                    # 可選：語音播報 AI 回覆
                    # speak_text(response)
        else:
            st.markdown("<h3 class='section-title'>⌨️ 文字輸入模式</h3>", unsafe_allow_html=True)
            with st.form("chat_form", clear_on_submit=True):  # 添加 clear_on_submit=True 提交後清空輸入框
                user_input = st.text_input("請輸入您的查詢", key="query_input", placeholder="在此輸入您的問題...")
                col_btn1, col_btn2 = st.columns([1, 3])
                with col_btn1:
                    submitted = st.form_submit_button("送出查詢")
            
            if submitted and user_input:
                if st.session_state.pending_offset is not None:
                    reply = user_input.strip().lower()
                    if reply in ["yes", "y"]:
                        adjustment_signal = f"[TempOffset]: {st.session_state.pending_offset}"
                        try:
                            s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                            s.connect(('localhost', 9999))
                            s.send(adjustment_signal.encode('utf-8'))
                            response_data = s.recv(1024)
                            s.close()
                            st.session_state.chat_history.append(("系統", f"✅ 調整完畢，已將溫度設定為 + {st.session_state.pending_offset}。"))
                        except Exception as e:
                            st.session_state.chat_history.append(("系統", f"❌ Socket error: {e}"))
                        st.session_state.pending_offset = None
                    elif reply in ["no", "n"]:
                        st.session_state.chat_history.append(("系統", "🚫 自動調整已取消。"))
                        st.session_state.pending_offset = None
                    else:
                        st.session_state.chat_history.append(("系統", "❓ 請回覆 'yes' 或 'no' 以確認是否自動調整。"))
                else:
                    st.session_state.chat_history.append(("使用者", user_input))
                    response = process_query(user_input)
                    st.session_state.chat_history.append(("系統", response))
                st.session_state.needs_rerun = True  # 標記需要重新執行
            
            # 播放回覆按鈕
            if st.session_state.chat_history and st.session_state.chat_history[-1][0] == "系統":
                if st.button("🔊 播放回覆", key="play_response"):
                    speak_text(st.session_state.chat_history[-1][1])
                    st.session_state.needs_rerun = True  # 標記需要重新執行
        
        st.markdown("</div>", unsafe_allow_html=True)
    
    # 自動重新執行以更新聊天記錄
    if st.session_state.needs_rerun:
        st.session_state.needs_rerun = False
        st.rerun()

if __name__ == '__main__':
    main()