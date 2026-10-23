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

import logging
import csv
import math

import numpy as np
from scipy.optimize import curve_fit

log = logging.getLogger(__name__)

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


def _log_time_model(digits, t0, a, c, alpha):
    """ln(T) con T = t0 + exp(a + c·L): t0 es el coste fijo de arrancar la
    herramienta (YAFU ~2s, CADO ~25s levantando servidor y clientes). Sin
    él, los puntos pequeños -- casi todo arranque -- aplanaban la curva:
    YAFU a 90 dígitos se estimaba en 34s y tardó 253s."""
    return np.log(t0 + np.exp(a + c * _l_shape(digits, alpha)))


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
    if len({p["digits"] for p in pts}) < 4:
        raise ValueError(
            f"Solo {len({p['digits'] for p in pts})} tamaños con éxito para '{method}'. "
            f"Hacen falta al menos 4 (el modelo tiene 3 parámetros)."
        )
    alpha = ALPHA_BY_METHOD.get(method, DEFAULT_ALPHA)
    digits = np.array([p["digits"] for p in pts], dtype=float)
    times = np.array([p["elapsed_seconds"] for p in pts])
    log_time = np.log(times)

    # Punto de partida: sin coste fijo, ajuste lineal de ln(T) sobre L.
    c0, a0 = np.polyfit(_l_shape(digits, alpha), log_time, 1)
    popt, _ = curve_fit(lambda d, t0, a, c: _log_time_model(d, t0, a, c, alpha),
                        digits, log_time, p0=[times.min() / 2, a0, max(c0, 1e-6)],
                        bounds=([0, -np.inf, 0], [np.inf, np.inf, np.inf]), maxfev=20000)

    # RAM: regresión log-lineal simple. No pretende capturar el salto de
    # la fase de matriz de GNFS, solo dar una primera cota razonable.
    ram = np.array([p["peak_ram_mb"] for p in pts])
    ram_slope, ram_intercept = np.polyfit(digits, np.log(np.maximum(ram, 1.0)), 1)

    return {"alpha": alpha, "t0": popt[0], "a": popt[1], "c": popt[2],
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

    log_time = _log_time_model(np.array([digits], dtype=float), m["t0"], m["a"], m["c"], m["alpha"])[0]
    seconds = float(np.exp(log_time))
    ram_mb = float(np.exp(m["ram_slope"] * digits + m["ram_intercept"]))

    return max(seconds, 0.0), max(ram_mb, 0.0)


def crossover_digits(slow_small="YAFU", fast_large="CADO-NFS", lo=40, hi=200,
                     csv_path="benchmark_results.csv"):
    """Menor tamaño (dígitos) desde el que `fast_large` se estima más rápido
    que `slow_small`, o None si no ocurre en [lo, hi]. Es el umbral natural
    de gnfs_min_digits."""
    for digits in range(lo, hi + 1):
        if predict(digits, fast_large, csv_path)[0] < predict(digits, slow_small, csv_path)[0]:
            return digits
    return None


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    for method in ("GMP-ECM", "YAFU", "CADO-NFS"):
        try:
            seconds, ram_mb = predict(80, method=method)
            log.info(f"{method}: N de 80 dígitos -> {seconds:.1f}s, {ram_mb:.0f}MB RAM")
        except ValueError as e:
            log.info(f"{method}: {e}")