"""
orchestrator/controller.py

Bucle de control central. No factoriza nada: decide cuántos hilos recibe
cada worker y cuándo matarlo, leyendo runtime/resources.json (CPU/RAM/
temperatura) y runtime/enemy_state.json (scoreboard rival).

`decide()` es una función pura (sin disco, sin procesos) para poder
testearla inyectando estados sintéticos y verificando qué Action produce
— tal como se planificó: "estados sintéticos... verificar que resize()/
kill() se llaman con los argumentos esperados".

Los workers (workers/*_worker.py, sobre BaseWorker) cumplen
`WorkerHandle` con un Popen real por grupo de proceso. "Redimensionar"
es matar y relanzar con otros hilos, con cooldown (MIN_RESIZE_INTERVAL)
en el propio worker, que puede ignorar un resize() -- por eso los hilos
actuales se leen del worker (`current_threads`) y no se llevan aquí.

La regla 3 (aborto por varianza) necesita `projected_finish_minute` en
runtime/gnfs_progress.json, que gnfs_worker.py aún no calcula: hoy esa
regla no se dispara nunca.
"""

from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass
from typing import List, Optional, Protocol


# --------------------------------------------------------------------------
# Contrato que deben cumplir los workers reales
# --------------------------------------------------------------------------

class WorkerHandle(Protocol):
    current_threads: int

    def start(self, n: int, threads: int) -> None: ...
    def resize(self, threads: int) -> None: ...
    def kill(self) -> None: ...
    def is_running(self) -> bool: ...


# --------------------------------------------------------------------------
# Configuración, estado y acciones
# --------------------------------------------------------------------------

@dataclass
class Config:
    total_threads: int = 10
    ecm_min_threads: int = 2
    gnfs_min_threads: int = 4
    ram_critical_pct: float = 90.0
    temp_critical_c: float = 90.0
    cpu_headroom_pct: float = 85.0
    staleness_seconds: float = 120.0
    d_safe_bits: int = 330 
    variance_abort_minute: int = 20
    ctf_duration_minutes: int = 60


@dataclass
class ControlContext:
    resources: Optional[dict]           # None si el monitor está caído/obsoleto
    enemy_state: Optional[dict]         # None si el watcher está caído/obsoleto
    gnfs_running: bool
    ecm_running: bool
    ecm_threads: int
    gnfs_elapsed_minutes: float
    gnfs_projected_finish_minute: Optional[float]


@dataclass
class Action:
    worker: str      # "gnfs" | "ecm"
    op: str          # "kill" | "resize"
    value: Optional[int] = None
    reason: str = ""


# --------------------------------------------------------------------------
# Lógica de decisión (pura, testeable sin tocar disco ni procesos)
# --------------------------------------------------------------------------

def decide(ctx: ControlContext, cfg: Config) -> List[Action]:
    actions: List[Action] = []

    # 1) Prioridad máxima: recursos críticos. Manda sobre cualquier otra regla.
    if ctx.resources is not None:
        ram = ctx.resources.get("ram_percent", 0)
        temp = ctx.resources.get("temperature", 0)
        if ram >= cfg.ram_critical_pct or temp >= cfg.temp_critical_c:
            if ctx.gnfs_running:
                actions.append(Action("gnfs", "kill",
                    reason=f"RAM={ram}% temp={temp}C por encima del límite crítico"))
            if ctx.ecm_running and ctx.ecm_threads > cfg.ecm_min_threads:
                actions.append(Action("ecm", "resize", cfg.ecm_min_threads,
                    reason="reducir carga por recursos críticos"))
            return actions

    # 2) Kill-switch matemático: el rival rompió algo mayor que nuestro D_safe.
    if ctx.enemy_state is not None and ctx.gnfs_running:
        enemy_max_bits = ctx.enemy_state.get("max_broken_bits", 0)
        if enemy_max_bits > cfg.d_safe_bits:
            actions.append(Action("gnfs", "kill",
                reason=f"rival rompió {enemy_max_bits} bits > D_safe={cfg.d_safe_bits}"))
            actions.append(Action("ecm", "resize", cfg.total_threads,
                reason="reasignar todos los hilos a ECM (billete de lotería)"))
            return actions

    # 3) Aborto por varianza: la proyección de GNFS no converge a tiempo.
    if (ctx.gnfs_running
            and ctx.gnfs_elapsed_minutes >= cfg.variance_abort_minute
            and ctx.gnfs_projected_finish_minute is not None
            and ctx.gnfs_projected_finish_minute > cfg.ctf_duration_minutes - 4):
        actions.append(Action("gnfs", "kill",
            reason=(f"proyección de fin en minuto "
                    f"{ctx.gnfs_projected_finish_minute:.1f} no converge a tiempo")))
        actions.append(Action("ecm", "resize", cfg.total_threads,
            reason="redirigir hilos a ECM tras abortar GNFS por varianza"))
        return actions

    # 4) Estado estable: AIMD sobre el cazador (ECM). El ancla mantiene su
    #    asignación base; el cazador crece/decrece según la contención real.
    if ctx.resources is not None:
        cpu = ctx.resources.get("cpu_percent", 100)
        max_ecm = cfg.total_threads - cfg.gnfs_min_threads
        if cpu < cfg.cpu_headroom_pct:
            if ctx.ecm_running and ctx.ecm_threads < max_ecm:
                actions.append(Action("ecm", "resize", ctx.ecm_threads + 1,
                    reason=f"margen de CPU ({cpu}%), incremento aditivo"))
        else:
            if ctx.ecm_running and ctx.ecm_threads > cfg.ecm_min_threads:
                new_threads = max(cfg.ecm_min_threads, ctx.ecm_threads // 2)
                actions.append(Action("ecm", "resize", new_threads,
                    reason=f"contención de CPU ({cpu}%), decrecimiento multiplicativo"))

    return actions


# --------------------------------------------------------------------------
# Bucle real: lee disco, ejecuta las acciones sobre workers reales
# --------------------------------------------------------------------------

class ControlLoop:
    def __init__(self, cfg: Config, gnfs: WorkerHandle, ecm: WorkerHandle,
                 runtime_dir: str = "runtime", tick_seconds: float = 10.0):
        self.cfg = cfg
        self.gnfs = gnfs
        self.ecm = ecm
        self.runtime_dir = runtime_dir
        self.tick_seconds = tick_seconds
        self._start_time = time.time()

    def _read_json_if_fresh(self, filename: str) -> Optional[dict]:
        path = os.path.join(self.runtime_dir, filename)
        if not os.path.exists(path):
            return None
        age = time.time() - os.path.getmtime(path)
        if age > self.cfg.staleness_seconds:
            print(f"[controller] {filename} obsoleto ({age:.1f}s) — ignorado")
            return None
        try:
            with open(path) as f:
                return json.load(f)
        except json.JSONDecodeError:
            return None

    def _build_context(self) -> ControlContext:
        elapsed = (time.time() - self._start_time) / 60
        gnfs_progress = self._read_json_if_fresh("gnfs_progress.json") or {}
        return ControlContext(
            resources=self._read_json_if_fresh("resources.json"),
            enemy_state=self._read_json_if_fresh("enemy_state.json"),
            gnfs_running=self.gnfs.is_running(),
            ecm_running=self.ecm.is_running(),
            ecm_threads=self.ecm.current_threads,
            gnfs_elapsed_minutes=elapsed,
            gnfs_projected_finish_minute=gnfs_progress.get("projected_finish_minute"),
        )

    def _apply(self, actions: List[Action]):
        for action in actions:
            worker = self.gnfs if action.worker == "gnfs" else self.ecm
            print(f"[controller] {action.worker}.{action.op}"
                  f"({action.value if action.value is not None else ''}) — {action.reason}")
            if action.op == "kill":
                worker.kill()
            elif action.op == "resize":
                worker.resize(action.value)

    def tick(self):
        """Una sola iteración: lee estado, decide, actúa. Sin sleep — para
        que quien la llame (main.py, o un bucle por-objetivo) controle el
        ritmo y la condición de parada."""
        ctx = self._build_context()
        actions = decide(ctx, self.cfg)
        self._apply(actions)
        return ctx, actions

    def run_forever(self):
        while True:
            self.tick()
            time.sleep(self.tick_seconds)

