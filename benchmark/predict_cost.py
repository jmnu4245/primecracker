"""
benchmark/predict_cost.py

Ajusta ln(T) = a + c * (ln N)^alpha * (ln ln N)^(1-alpha) — la forma de
la notación L — sobre los puntos exitosos de benchmark_results.csv, por
método, y expone predict(digits, method) -> (segundos, RAM_mb).

IMPORTANTE: con solo unos pocos tamaños medidos esto es una estimación
de orden de magnitud, no una predicción exacta — es precisamente la
razón por la que evitamos los umbrales de bits fijos de la teoría pura.
Cuantos más tamaños midas con run_benchmark.py, más fiable se vuelve.
"""

import csv
import math

import numpy as np
from scipy.optimize import curve_fit

# alpha por método: GNFS es subexponencial "fuerte" (1/3); SIQS/YAFU y
# ECM (aproximado, solo válido mientras el benchmark use semiprimos
# balanceados, donde el factor p escala junto con los dígitos de N) son 1/2.
ALPHA_BY_METHOD = {
    "CADO-NFS": 1 / 3,
    "YAFU": 1 / 2,
    "GMP-ECM": 1 / 2,
}
DEFAULT_ALPHA = 1 / 2

# Las claves de arriba son los nombres que escribe run_benchmark.py en la
# columna `method` del CSV. Antes se usaban "GNFS"/"YAFU (SIQS)", que no
# aparecen en el CSV: predict() fallaba siempre y main.py caía al fallback.
METHOD_ALIASES = {
    "GNFS": "CADO-NFS",
    "YAFU (SIQS)": "YAFU",
    "SIQS": "YAFU",
    "ECM": "GMP-ECM",
}


def canonical_method(method):
    return METHOD_ALIASES.get(method, method)


def _l_shape(digits, alpha):
    digits = np.asarray(digits, dtype=float)
    ln_n = digits * math.log(10)          # dígitos -> ln(N) aproximado
    ln_ln_n = np.log(ln_n)
    return (ln_n ** alpha) * (ln_ln_n ** (1 - alpha))


def _log_time_model(digits, a, c, alpha):
    return a + c * _l_shape(digits, alpha)


def load_results(csv_path="benchmark_results.csv"):
    rows = []
    with open(csv_path, newline="") as f:
        for row in csv.DictReader(f):
            if row["success"] in ("True", "true", "1"):
                rows.append({
                    "digits": int(row["digits"]),
                    "method": row["method"],
                    "elapsed_seconds": float(row["elapsed_seconds"]),
                    "peak_ram_mb": float(row["peak_ram_mb"]),
                })
    return rows


def fit_method(rows, method):
    method = canonical_method(method)
    pts = [r for r in rows if r["method"] == method]
    if len(pts) < 3:
        raise ValueError(
            f"Solo {len(pts)} puntos exitosos para '{method}'. "
            f"Hacen falta al menos 3 tamaños distintos de dígitos para ajustar la curva."
        )
    alpha = ALPHA_BY_METHOD.get(method, DEFAULT_ALPHA)
    digits = np.array([p["digits"] for p in pts])
    log_time = np.log(np.array([p["elapsed_seconds"] for p in pts]))

    popt, _ = curve_fit(lambda d, a, c: _log_time_model(d, a, c, alpha),
                         digits, log_time, maxfev=10000)

    # RAM: regresión log-lineal simple. No pretende capturar el salto de
    # la fase de matriz de GNFS, solo dar una primera cota razonable.
    ram = np.array([p["peak_ram_mb"] for p in pts])
    ram_slope, ram_intercept = np.polyfit(digits, np.log(np.maximum(ram, 1.0)), 1)

    return {"alpha": alpha, "a": popt[0], "c": popt[1],
            "ram_slope": ram_slope, "ram_intercept": ram_intercept,
            "n_points": len(pts)}


_MODEL_CACHE = {}


def predict(digits, method="YAFU", csv_path="benchmark_results.csv"):
    """(segundos_estimados, ram_estimada_mb) para un N de `digits` dígitos,
    extrapolando el ajuste sobre benchmark_results.csv."""
    method = canonical_method(method)
    key = (method, csv_path)
    if key not in _MODEL_CACHE:
        rows = load_results(csv_path)
        _MODEL_CACHE[key] = fit_method(rows, method)
    m = _MODEL_CACHE[key]

    log_time = _log_time_model(np.array([digits]), m["a"], m["c"], m["alpha"])[0]
    seconds = float(np.exp(log_time))
    ram_mb = float(np.exp(m["ram_slope"] * digits + m["ram_intercept"]))

    return max(seconds, 0.0), max(ram_mb, 0.0)


if __name__ == "__main__":
    for method in ("GMP-ECM", "YAFU", "CADO-NFS"):
        try:
            seconds, ram_mb = predict(80, method=method)
            print(f"{method}: N de 80 dígitos -> {seconds:.1f}s, {ram_mb:.0f}MB RAM")
        except ValueError as e:
            print(f"{method}: {e}")