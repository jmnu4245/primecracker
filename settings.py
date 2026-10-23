"""
settings.py

Ajustes operativos del proyecto, leídos de config/settings.toml. Todo
tiene un valor por defecto: el archivo solo necesita lo que se quiera
cambiar. Una clave desconocida es un error (una errata no debe pasar
desapercibida y dejar el valor por defecto en silencio).
"""

import os
import tomllib
from dataclasses import dataclass, fields, replace

SETTINGS_PATH = os.path.join("config", "settings.toml")


@dataclass(frozen=True)
class Settings:
    # Binarios (ecm_bin, yafu_bin, cado-nfs/). build/setup_toolchain.sh
    # enlaza aquí la variante compatible con este CPU.
    toolchain_dir: str = os.path.join("bin", "active")

    # Hilos. 0 = automático a partir de los núcleos del equipo.
    total_threads: int = 0
    anchor_threads: int = 0      # YAFU/GNFS; automático: todos (la mitad con ecm_parallel)
    ecm_min_threads: int = 0     # solo con ecm_parallel; automático: una sexta parte

    # Pretest de ECM antes de GNFS (>= gnfs_min_digits), con todos los
    # núcleos: atrapa factores de hasta ~30 dígitos (B1=250e3, ~450 curvas
    # dan ~63% para 30 dígitos) en segundos, antes de comprometer decenas
    # de minutos de GNFS. Con YAFU no hace falta: su factor() ya hace su
    # propio pretest (rho, p-1, ECM) antes de SIQS.
    ecm_pretest_b1: int = 250_000
    ecm_pretest_curves: int = 450
    ecm_pretest_seconds: float = 120   # 0 = sin pretest

    # Modos antiguos, desactivados por defecto:
    # - ecm_parallel: ECM en paralelo con el ancla durante todo el ataque,
    #   repartiendo hilos con AIMD. Con semiprimos equilibrados ECM casi
    #   nunca acierta y le quita la mitad de los núcleos a GNFS.
    # - enemy_kill_switch: abortar GNFS si el rival ha roto más de
    #   d_safe_bits; depende de actualizar state.json a mano en plena
    #   competición.
    ecm_parallel: bool = False
    enemy_kill_switch: bool = False

    # Límites del controlador
    ram_critical_pct: float = 90.0
    temp_critical_c: float = 85.0
    cpu_headroom_pct: float = 85.0
    d_safe_bits: int = 330

    # Elección de método y reparto del tiempo
    # Desde aquí GNFS en vez de YAFU (SIQS). 93 = cruce medido en este equipo
    # (benchmark_results.csv, 12 hilos): a 90 dígitos YAFU 253s / CADO 310s,
    # a 95 YAFU 628s / CADO 448s. Recalcular tras un benchmark nuevo con
    # benchmark.predict_cost.crossover_digits().
    gnfs_min_digits: int = 93
    total_minutes: float = 60          # sin --minutes ni state.json
    default_target_seconds: float = 180  # sin predicción para el método
    min_target_seconds: float = 60     # por debajo no merece la pena lanzar nada
    budget_margin: float = 1.5         # holgura sobre la estimación del benchmark

    def threads(self):
        """(total, ancla, ecm_min) con los automáticos resueltos."""
        total = self.total_threads or os.cpu_count() or 4
        default_anchor = max(1, total // 2) if self.ecm_parallel else total
        anchor = self.anchor_threads or default_anchor
        ecm_min = self.ecm_min_threads or max(1, total // 6)
        return total, anchor, ecm_min

    def binary(self, name):
        return os.path.join(self.toolchain_dir, name)


def load(path=SETTINGS_PATH):
    if not os.path.exists(path):
        return Settings()
    with open(path, "rb") as f:
        data = tomllib.load(f)
    known = {f.name for f in fields(Settings)}
    unknown = set(data) - known
    if unknown:
        raise ValueError(f"{path}: claves desconocidas {sorted(unknown)}; "
                         f"válidas: {sorted(known)}")
    return replace(Settings(), **data)


SETTINGS = load()
