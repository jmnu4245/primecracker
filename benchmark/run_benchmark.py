"""
benchmark/run_benchmark.py

Genera semiprimos balanceados de tamaño creciente (p, q de ~mitad de
dígitos cada uno, el peor caso para GNFS/SIQS) y mide cuánto tarda cada
worker en factorizarlos y cuánta RAM pico usa. Escribe cada fila al CSV
en cuanto termina, así una ejecución larga no pierde todo si se corta a
mitad de camino.

Uso:
    python3 -m benchmark.run_benchmark --start 10 --stop 50 --step 10 --timeout 120
"""

import argparse
import csv
import glob
import os
import random
import time

import psutil

from workers.gnfs_worker import GNFSWorker
from workers.gpu_cleanup import is_probable_prime
from workers.ecm_worker import ECMWorker
from workers.yafu_worker import YAFUWorker

# TODO: añadir GNFSWorker aquí en cuanto exista su refactor a start/kill/is_running.
WORKER_FACTORIES = [
    lambda: ECMWorker(),
    lambda: YAFUWorker(),
    lambda: GNFSWorker()
]

CSV_FIELDS = ["digits", "method", "success", "elapsed_seconds", "timed_out", "peak_ram_mb"]


def random_prime(digits):
    lower, upper = 10 ** (digits - 1), 10 ** digits - 1
    while True:
        candidate = random.randint(lower, upper) | 1
        if is_probable_prime(candidate):
            return candidate


def make_semiprime(total_digits):
    half = max(total_digits // 2, 1)
    p = random_prime(half)
    q = random_prime(total_digits - half)
    return p * q


def _check_factor_found(target_id, since_ts):
    """Busca el JSON que escribió el worker (ver base_worker._write_success)
    y devuelve (éxito, segundos_reportados)."""
    candidates = [
        path for path in glob.glob(f"runtime/factor_found_{target_id}_*.json")
        if os.path.getmtime(path) >= since_ts - 1
    ]
    if not candidates:
        return False, None
    latest = max(candidates, key=os.path.getmtime)
    import json
    with open(latest) as f:
        payload = json.load(f)
    return True, payload.get("time_elapsed_seconds")


def measure(worker, timeout):
    start = time.time()
    try:
        proc = psutil.Process(worker.process.pid)
    except Exception:
        proc = None

    peak_ram_mb = 0.0
    while worker.is_running():
        if proc is not None:
            try:
                peak_ram_mb = max(peak_ram_mb, proc.memory_info().rss / (1024 ** 2))
            except psutil.NoSuchProcess:
                pass
        time.sleep(0.5)

    elapsed = time.time() - start
    success, reported_seconds = _check_factor_found(worker.target_id, start)

    return {
        "success": success,
        "elapsed_seconds": reported_seconds if success else round(elapsed, 2),
        "timed_out": (not success) and elapsed >= timeout - 1,
        "peak_ram_mb": round(peak_ram_mb, 1),
    }


def _append_csv(rows, path):
    file_exists = os.path.exists(path)
    with open(path, "a", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=CSV_FIELDS)
        if not file_exists:
            writer.writeheader()
        writer.writerows(rows)


def run_benchmark(digit_sizes, timeout_per_run, output_csv):
    for digits in digit_sizes:
        n = make_semiprime(digits)
        actual_digits = len(str(n))
        print(f"\n=== N de {actual_digits} dígitos ===")

        rows = []
        for factory in WORKER_FACTORIES:
            worker = factory()
            print(f"[*] {worker.method_name}...")
            worker.start(n, timeout=timeout_per_run)
            result = measure(worker, timeout_per_run)
            rows.append({"digits": actual_digits, "method": worker.method_name, **result})
            status = "OK" if result["success"] else ("TIMEOUT" if result["timed_out"] else "SIN FACTOR")
            print(f"    -> {status} en {result['elapsed_seconds']}s, "
                  f"pico RAM {result['peak_ram_mb']}MB")

        _append_csv(rows, output_csv)

    print(f"\nBenchmark completo. Resultados en {output_csv}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--start", type=int, default=10)
    parser.add_argument("--stop", type=int, default=50)
    parser.add_argument("--step", type=int, default=10)
    parser.add_argument("--timeout", type=int, default=120,
                         help="segundos máximos por intento antes de darlo por timeout")
    parser.add_argument("--output", default="benchmark_results.csv")
    args = parser.parse_args()

    run_benchmark(range(args.start, args.stop + 1, args.step), args.timeout, args.output)