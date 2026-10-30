"""
benchmark/machine.py

Factor de velocidad de esta máquina respecto a la del benchmark
(benchmark_results.csv), para que las estimaciones de coste valgan en
cualquier equipo:

    tiempo_aquí ≈ tiempo_benchmark × factor        (factor < 1: más rápida)

Dos formas de obtenerlo, por orden de preferencia:

1. Calibración: factorizar con YAFU un número fijo de 70 dígitos y
   compararlo con lo que tardó en la máquina de referencia. Mide el
   rendimiento real (IPC, turbo, memoria, AVX), ~15 s.
2. Núcleos × GHz: estimación gruesa a partir de las especificaciones. No
   capta las diferencias de arquitectura (fácilmente ±30-50%); solo como
   respaldo si la calibración falla.

El resultado se guarda en config/machine.json y se reutiliza mientras no
cambien la CPU, los hilos o la frecuencia máxima (activar o desactivar el
turbo cambia la frecuencia máxima y fuerza a recalibrar).
"""

import json
import logging
import os
import platform
import time

import psutil

log = logging.getLogger(__name__)

MACHINE_PATH = os.path.join("config", "machine.json")

# Máquina en la que se midió benchmark_results.csv.
REFERENCE = {
    "cpu_model": "Intel(R) Core(TM) i7-9750H CPU @ 2.60GHz",
    "physical_cores": 6,
    "logical_cpus": 12,
    "max_mhz": 2600.0,             # turbo desactivado durante el benchmark
    "calibration_seconds": 6.48,   # mínimo de 3 ejecuciones (6.48, 6.53, 6.72)
}

# Semiprimo fijo de 70 dígitos (dos primos de 35) para calibrar.
CALIBRATION_N = 886800505066058115068041452277099009100334398920537868906335095848731
CALIBRATION_RUNS = 2

# Un hilo extra por núcleo (hyperthreading/SMT) rinde mucho menos que un
# núcleo: en factorización, típicamente un 20-30% más por núcleo.
SMT_GAIN = 0.25


def cpu_model():
    try:
        with open("/proc/cpuinfo") as f:
            for line in f:
                if line.startswith("model name"):
                    return line.split(":", 1)[1].strip()
    except OSError:
        pass
    return platform.processor() or "desconocida"


def current_specs():
    freq = psutil.cpu_freq()
    return {
        "cpu_model": cpu_model(),
        "physical_cores": psutil.cpu_count(logical=False) or os.cpu_count() or 1,
        "logical_cpus": os.cpu_count() or 1,
        "max_mhz": float(freq.max or freq.current) if freq else 0.0,
    }


def capacity(specs):
    """Capacidad de cómputo relativa: núcleos efectivos × MHz."""
    physical, logical = specs["physical_cores"], specs["logical_cpus"]
    return (physical + SMT_GAIN * max(logical - physical, 0)) * specs["max_mhz"]


def factor_from_specs(specs=None):
    specs = specs or current_specs()
    if not specs["max_mhz"]:
        return 1.0
    return capacity(REFERENCE) / capacity(specs)


def calibrate(runs=CALIBRATION_RUNS):
    """Factor medido: mejor tiempo de YAFU sobre CALIBRATION_N aquí, entre
    el de la referencia. None si YAFU no está disponible o falla."""
    from workers.yafu_worker import YAFUWorker
    best = None
    for _ in range(runs):
        worker = YAFUWorker()
        start = time.monotonic()
        try:
            worker.start(CALIBRATION_N, threads=os.cpu_count() or 1, timeout=600)
            worker.found.wait(600)
        except OSError as e:
            log.warning(f"[!] No se pudo calibrar con YAFU: {e}")
            return None
        elapsed = time.monotonic() - start
        ok = worker.result is not None
        if worker.result_path and os.path.exists(worker.result_path):
            os.remove(worker.result_path)   # no es un objetivo: que no lo vea el collector
        worker.discard_workdir()
        if not ok:
            log.warning("[!] La calibración con YAFU no factorizó el número de prueba.")
            return None
        best = elapsed if best is None else min(best, elapsed)
    return best / REFERENCE["calibration_seconds"]


def _same_machine(saved, specs):
    return all(saved.get(k) == specs[k] for k in ("cpu_model", "logical_cpus", "max_mhz"))


def load_factor(path=MACHINE_PATH):
    """Factor guardado para esta máquina, o None si no hay o es de otra
    configuración (otra CPU, otros hilos, turbo cambiado)."""
    try:
        with open(path) as f:
            saved = json.load(f)
    except (OSError, ValueError):
        return None
    return saved["factor"] if _same_machine(saved, current_specs()) else None


def ensure_factor(path=MACHINE_PATH, allow_calibration=True):
    """Factor de esta máquina: guardado, o calibrado ahora (y guardado), o
    por especificaciones como último recurso."""
    factor = load_factor(path)
    if factor is not None:
        return factor
    specs = current_specs()
    method = "calibración"
    factor = calibrate() if allow_calibration else None
    if factor is None:
        method = "núcleos × GHz"
        factor = factor_from_specs(specs)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        json.dump({**specs, "factor": factor, "method": method,
                   "specs_factor": factor_from_specs(specs),
                   "measured_at": time.strftime("%Y-%m-%d %H:%M")}, f, indent=2)
    log.info(f"[*] Máquina: {specs['cpu_model']}, {specs['physical_cores']} núcleos / "
             f"{specs['logical_cpus']} hilos, {specs['max_mhz']:.0f} MHz máx.")
    log.info(f"[*] Factor de velocidad ({method}): {factor:.2f} "
             f"(1.00 = máquina del benchmark; <1 = más rápida).")
    return factor


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    specs = current_specs()
    print(f"Especificaciones: {specs}")
    print(f"Factor por núcleos × GHz: {factor_from_specs(specs):.2f}")
    measured = calibrate()
    print(f"Factor calibrado: {measured:.2f}" if measured else "Calibración fallida")
