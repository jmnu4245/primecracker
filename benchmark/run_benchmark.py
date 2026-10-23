"""
benchmark/run_benchmark.py

Genera semiprimos balanceados de tamaño creciente (p, q de ~mitad de
dígitos cada uno, el peor caso para GNFS/SIQS) y mide cuánto tarda cada
worker en factorizarlos y cuánta RAM pico usa. Escribe cada fila al CSV
en cuanto termina, así una ejecución larga no pierde todo si se corta a
mitad de camino.

Uso:
    python3 -m benchmark.run_benchmark --start 10 --stop 50 --step 10 --timeout 120
    python3 -m benchmark.run_benchmark --start 90 --stop 100 --step 5 \\
        --methods CADO-NFS YAFU --threads 12 --timeout 3600
"""

import logging
import argparse
import csv
import os
import random
import time

import psutil

from workers.gnfs_worker import GNFSWorker
from workers.quick_factor import is_probable_prime
from workers.ecm_worker import ECMWorker
from workers.yafu_worker import YAFUWorker

log = logging.getLogger(__name__)

WORKER_FACTORIES = {
    "GMP-ECM": ECMWorker,
    "YAFU": YAFUWorker,
    "CADO-NFS": GNFSWorker,
}

# Por debajo de esto CADO-NFS no tiene fichero de parámetros (params.c60
# es el menor) y termina con error en menos de un segundo: medirlo solo
# metía filas de fallo sin información en el CSV.
MIN_DIGITS = {"CADO-NFS": 60}

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


def _worker_processes(worker):
    """Procesos que trabajan para el worker: su árbol, y en CADO además los
    clientes que se lanzan con --daemon (se desenganchan del árbol, y son
    los que ejecutan la criba con `las`, lo que más memoria usa)."""
    procs = []
    try:
        root = psutil.Process(worker.process.pid)
        procs = [root] + root.children(recursive=True)
    except psutil.NoSuchProcess:
        pass
    workdir = getattr(worker, "workdir", None)
    if workdir:
        for p in psutil.process_iter(["cmdline"]):
            if workdir in " ".join(p.info["cmdline"] or []):
                procs.append(p)
                try:
                    procs.extend(p.children(recursive=True))
                except psutil.NoSuchProcess:
                    pass
    return {p.pid: p for p in procs}.values()


def _rss_mb(worker):
    total = 0
    for p in _worker_processes(worker):
        try:
            total += p.memory_info().rss
        except psutil.NoSuchProcess:
            pass
    return total / (1024 ** 2)


def measure(worker, timeout):
    start = time.monotonic()  # sin contar suspensiones del equipo
    peak_ram_mb = 0.0
    while worker.is_running():
        peak_ram_mb = max(peak_ram_mb, _rss_mb(worker))
        time.sleep(1.0)

    elapsed = time.monotonic() - start
    success = worker.found.is_set()
    return {
        "success": success,
        "elapsed_seconds": round(elapsed, 2),
        "timed_out": (not success) and elapsed >= timeout - 1,
        "peak_ram_mb": round(peak_ram_mb, 1),
    }


def _cleanup(worker):
    # El archivo de resultado no es de ningún objetivo real: si se queda en
    # runtime/, collector lo registraría si coincidiera con un objetivo.
    if worker.result_path and os.path.exists(worker.result_path):
        os.remove(worker.result_path)
    discard = getattr(worker, "discard_workdir", None)
    if discard:
        discard()
    else:
        worker.kill()


def _append_csv(rows, path):
    file_exists = os.path.exists(path)
    with open(path, "a", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=CSV_FIELDS)
        if not file_exists:
            writer.writeheader()
        writer.writerows(rows)


def run_benchmark(digit_sizes, timeout_per_run, output_csv, methods, threads):
    for digits in digit_sizes:
        n = make_semiprime(digits)
        actual_digits = len(str(n))
        log.info(f"\n=== N de {actual_digits} dígitos ===")

        rows = []
        for method in methods:
            if actual_digits < MIN_DIGITS.get(method, 0):
                log.info(f"[*] {method}: se salta (< {MIN_DIGITS[method]} dígitos)")
                continue
            worker = WORKER_FACTORIES[method]()
            log.info(f"[*] {method} con {threads} hilos...")
            worker.start(n, threads=threads, timeout=timeout_per_run)
            try:
                result = measure(worker, timeout_per_run)
            finally:
                _cleanup(worker)
            rows.append({"digits": actual_digits, "method": method, **result})
            status = "OK" if result["success"] else ("TIMEOUT" if result["timed_out"] else "SIN FACTOR")
            log.info(f"    -> {status} en {result['elapsed_seconds']}s, "
                  f"pico RAM {result['peak_ram_mb']}MB")

        _append_csv(rows, output_csv)

    log.info(f"\nBenchmark completo. Resultados en {output_csv}")


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    parser = argparse.ArgumentParser()
    parser.add_argument("--start", type=int, default=10)
    parser.add_argument("--stop", type=int, default=50)
    parser.add_argument("--step", type=int, default=10)
    parser.add_argument("--timeout", type=int, default=120,
                        help="segundos máximos por intento antes de darlo por timeout")
    parser.add_argument("--methods", nargs="+", default=list(WORKER_FACTORIES),
                        choices=list(WORKER_FACTORIES))
    parser.add_argument("--threads", type=int, default=os.cpu_count() or 4)
    parser.add_argument("--output", default="benchmark_results.csv")
    args = parser.parse_args()

    run_benchmark(range(args.start, args.stop + 1, args.step), args.timeout,
                  args.output, args.methods, args.threads)
