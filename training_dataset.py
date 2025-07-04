import pandas as pd
import numpy as np

from sklearn.preprocessing import PolynomialFeatures, StandardScaler
from sklearn.multioutput import MultiOutputRegressor
from sklearn.pipeline import make_pipeline
from sklearn.linear_model import Ridge
from sklearn.metrics import mean_squared_error

from joblib import dump


def load_and_preprocess(path: str, sep: str = '\t') -> pd.DataFrame:
    """
    載入並預處理資料：讀取 CSV，移除空值。
    """
    df = pd.read_csv(path, sep=sep)
    df = df.dropna()
    return df


def train_models(df: pd.DataFrame):
    """
    訓練能耗與誤差模型，回傳模型與訓練資料。
    """
    # 特徵與目標
    X = df[['RPM', 'Hour', 'TempOffset']]
    y_energy = df[['CoolerPower', 'MachinePower']]
    y_error = df[['AvgError', 'MaxError']]

    # 能耗模型：10 次多項式 + 標準化 + Ridge
    energy_pipeline = make_pipeline(
        PolynomialFeatures(degree=10, include_bias=True),
        StandardScaler(),
        Ridge(alpha=0.03)
    )
    energy_model = MultiOutputRegressor(energy_pipeline)
    energy_model.fit(X, y_energy)

    # 誤差模型：5 次多項式 + 標準化 + Ridge
    error_pipeline = make_pipeline(
        PolynomialFeatures(degree=5, include_bias=True),
        StandardScaler(),
        Ridge(alpha=0.1)
    )
    error_model = MultiOutputRegressor(error_pipeline)
    error_model.fit(X, y_error)

    return energy_model, error_model, X, y_energy, y_error


def evaluate_models(energy_model, error_model, X, y_energy, y_error):
    """
    計算並回傳每個目標變數的 MSE。
    """
    energy_pred = energy_model.predict(X)
    error_pred = error_model.predict(X)

    mse_energy = mean_squared_error(y_energy, energy_pred, multioutput='raw_values')
    mse_error = mean_squared_error(y_error, error_pred, multioutput='raw_values')

    return mse_energy, mse_error


def main():
    # 資料路徑
    data_path = r"C:\Users\user\Desktop\python_data\Cooling_Machine_Data_EN.csv"

    # 1. 載入與預處理
    df = load_and_preprocess(data_path)

    # 2. 訓練模型
    energy_model, error_model, X, y_energy, y_error = train_models(df)

    # 3. 預測示例
    X_new = X.iloc[:5]
    energy_pred = energy_model.predict(X_new)
    error_pred = error_model.predict(X_new)
    print("—— 能耗預測（CoolerPower, MachinePower） ——")
    print(energy_pred)
    print("\n—— 誤差預測（AvgError, MaxError） ——")
    print(error_pred)

    # 4. MSE 評估
    mse_energy, mse_error = evaluate_models(energy_model, error_model, X, y_energy, y_error)
    print(f"CoolerPower MSE = {mse_energy[0]:.4f}")
    print(f"MachinePower MSE = {mse_energy[1]:.4f}")
    print(f"AvgError   MSE   = {mse_error[0]:.4f}")
    print(f"MaxError   MSE   = {mse_error[1]:.4f}")

    # 5. 儲存模型
    dump(energy_model, 'energy_model_poly_ridge.joblib')
    dump(error_model, 'error_model_poly_ridge.joblib')


if __name__ == '__main__':
    main()
