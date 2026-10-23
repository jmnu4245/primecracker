"""
Tests de orchestrator/controller.py.

decide() es pura: se le inyectan estados sintéticos y se comprueba qué
acciones produce. ControlLoop se prueba con workers falsos que registran
las llamadas, sin lanzar procesos.
"""

import json

import pytest

from orchestrator.controller import Action, Config, ControlContext, ControlLoop, decide


def ctx(**overrides):
    base = dict(
        resources={"cpu_percent": 50, "ram_percent": 40, "temperature": 60},
        enemy_state=None,
        gnfs_running=True,
        ecm_running=True,
        ecm_threads=2,
        gnfs_elapsed_minutes=1.0,
        gnfs_projected_finish_minute=None,
    )
    base.update(overrides)
    return ControlContext(**base)


def ops(actions):
    return [(a.worker, a.op, a.value) for a in actions]


CFG = Config(total_threads=12, ecm_min_threads=2, gnfs_min_threads=6,
             ram_critical_pct=90.0, temp_critical_c=85.0, cpu_headroom_pct=85.0,
             d_safe_bits=330, variance_abort_minute=20, deadline_minutes=60,
             finish_margin_minutes=4, enemy_kill_switch=True, aimd=True)


# ----------------------- regla 1: recursos críticos -----------------------

def test_ram_critica_mata_gnfs_y_reduce_ecm():
    c = ctx(resources={"cpu_percent": 50, "ram_percent": 95, "temperature": 60}, ecm_threads=5)
    assert ops(decide(c, CFG)) == [("gnfs", "kill", None), ("ecm", "resize", 2)]

def test_temperatura_critica_manda_sobre_kill_switch():
    c = ctx(resources={"cpu_percent": 50, "ram_percent": 40, "temperature": 90},
            enemy_state={"max_broken_bits": 1000})
    assert ops(decide(c, CFG)) == [("gnfs", "kill", None)]

def test_recursos_criticos_sin_workers_no_hace_nada():
    c = ctx(resources={"ram_percent": 99}, gnfs_running=False, ecm_running=False)
    assert decide(c, CFG) == []


# ----------------------- regla 2: kill-switch -----------------------

def test_kill_switch_si_rival_supera_d_safe():
    c = ctx(enemy_state={"max_broken_bits": 400})
    assert ops(decide(c, CFG)) == [("gnfs", "kill", None), ("ecm", "resize", 12)]

def test_kill_switch_no_se_dispara_en_el_limite():
    c = ctx(enemy_state={"max_broken_bits": 330})
    assert ("gnfs", "kill", None) not in ops(decide(c, CFG))


# ----------------------- regla 3: aborto por varianza -----------------------

def test_varianza_aborta_si_la_proyeccion_no_llega():
    c = ctx(gnfs_elapsed_minutes=25, gnfs_projected_finish_minute=58)
    assert ops(decide(c, CFG)) == [("gnfs", "kill", None), ("ecm", "resize", 12)]

def test_varianza_no_aborta_antes_del_minuto_de_corte():
    c = ctx(gnfs_elapsed_minutes=10, gnfs_projected_finish_minute=58)
    assert ("gnfs", "kill", None) not in ops(decide(c, CFG))

def test_varianza_no_aborta_si_llega_a_tiempo():
    c = ctx(gnfs_elapsed_minutes=25, gnfs_projected_finish_minute=40)
    assert ("gnfs", "kill", None) not in ops(decide(c, CFG))


# ----------------------- regla 4: AIMD sobre ECM -----------------------

def test_aimd_incremento_aditivo_con_margen():
    assert ops(decide(ctx(ecm_threads=3), CFG)) == [("ecm", "resize", 4)]

def test_aimd_tope_en_hilos_libres_de_gnfs():
    assert decide(ctx(ecm_threads=6), CFG) == []   # 12 - 6 de gnfs

def test_aimd_decrecimiento_multiplicativo_con_contencion():
    c = ctx(resources={"cpu_percent": 99, "ram_percent": 40, "temperature": 60}, ecm_threads=6)
    assert ops(decide(c, CFG)) == [("ecm", "resize", 3)]

def test_aimd_no_baja_del_minimo():
    c = ctx(resources={"cpu_percent": 99, "ram_percent": 40, "temperature": 60}, ecm_threads=2)
    assert decide(c, CFG) == []

def test_sin_datos_de_recursos_no_toca_ecm():
    assert decide(ctx(resources=None), CFG) == []


# ----------------------- ControlLoop con workers falsos -----------------------

class FakeWorker:
    def __init__(self, threads, running=True, honour_resize=True):
        self.current_threads = threads
        self.running = running
        self.honour_resize = honour_resize
        self.calls = []

    def start(self, n, threads): self.calls.append(("start", threads))
    def kill(self):
        self.calls.append(("kill",))
        self.running = False
    def is_running(self): return self.running
    def resize(self, threads):
        self.calls.append(("resize", threads))
        if self.honour_resize:
            self.current_threads = threads


@pytest.fixture
def runtime(tmp_path):
    def write(name, data):
        (tmp_path / name).write_text(json.dumps(data))
    write("resources.json", {"cpu_percent": 10, "ram_percent": 40, "temperature": 50})
    return tmp_path, write

def test_loop_aplica_acciones_a_los_workers(runtime):
    path, write = runtime
    write("resources.json", {"cpu_percent": 10, "ram_percent": 95, "temperature": 50})
    gnfs, ecm = FakeWorker(6), FakeWorker(4)
    ControlLoop(CFG, gnfs=gnfs, ecm=ecm, runtime_dir=str(path)).tick()
    assert gnfs.calls == [("kill",)]
    assert ecm.calls == [("resize", 2)]

def test_loop_lee_hilos_reales_del_worker(runtime):
    # El worker ignora el resize (cooldown): el siguiente tick debe pedir
    # de nuevo 3, no 4 -- antes el controlador llevaba su propia cuenta y
    # se desincronizaba.
    path, _ = runtime
    ecm = FakeWorker(2, honour_resize=False)
    loop = ControlLoop(CFG, gnfs=FakeWorker(6), ecm=ecm, runtime_dir=str(path))
    loop.tick()
    loop.tick()
    assert ecm.calls == [("resize", 3), ("resize", 3)]

def test_loop_ignora_estado_obsoleto(runtime):
    import os, time
    path, write = runtime
    write("resources.json", {"cpu_percent": 10, "ram_percent": 99, "temperature": 50})
    old = time.time() - 1000
    os.utime(path / "resources.json", (old, old))
    gnfs = FakeWorker(6)
    ControlLoop(CFG, gnfs=gnfs, ecm=FakeWorker(2), runtime_dir=str(path)).tick()
    assert gnfs.calls == []   # RAM al 99% pero el dato es viejo: no se actúa

def test_loop_tolera_json_corrupto(runtime):
    path, _ = runtime
    (path / "resources.json").write_text("{a medio escribir")
    _, actions = ControlLoop(CFG, gnfs=FakeWorker(6), ecm=FakeWorker(2), runtime_dir=str(path)).tick()
    assert actions == []


def test_loop_proyeccion_del_mismo_objetivo(runtime):
    path, write = runtime
    gnfs = FakeWorker(6)
    gnfs.current_n = 10403
    loop = ControlLoop(CFG, gnfs=gnfs, ecm=FakeWorker(6), runtime_dir=str(path))
    write("gnfs_progress.json", {"target_n": "10403",
                                 "projected_finish_at": loop._start_time + 30 * 60})
    assert loop._build_context().gnfs_projected_finish_minute == pytest.approx(30)

def test_loop_ignora_proyeccion_de_otro_objetivo(runtime):
    path, write = runtime
    gnfs = FakeWorker(6)
    gnfs.current_n = 10403
    loop = ControlLoop(CFG, gnfs=gnfs, ecm=FakeWorker(6), runtime_dir=str(path))
    write("gnfs_progress.json", {"target_n": "999", "projected_finish_at": loop._start_time + 90 * 60})
    assert loop._build_context().gnfs_projected_finish_minute is None

def test_temperatura_desconocida_no_dispara_ni_rompe():
    c = ctx(resources={"cpu_percent": 50, "ram_percent": 40, "temperature": None}, ecm_threads=3)
    assert ops(decide(c, CFG)) == [("ecm", "resize", 4)]


# ----------------------- reglas opcionales desactivadas -----------------------

def test_por_defecto_sin_kill_switch_ni_aimd():
    from dataclasses import replace
    cfg = replace(CFG, enemy_kill_switch=False, aimd=False)
    assert decide(ctx(enemy_state={"max_broken_bits": 1000}), cfg) == []
    assert decide(ctx(ecm_threads=3), cfg) == []
    # Las de seguridad siguen activas
    hot = ctx(resources={"cpu_percent": 50, "ram_percent": 95, "temperature": 60})
    assert ("gnfs", "kill", None) in ops(decide(hot, cfg))
    late = ctx(gnfs_elapsed_minutes=25, gnfs_projected_finish_minute=58)
    assert ("gnfs", "kill", None) in ops(decide(late, cfg))

def test_config_por_defecto_desactiva_ambas():
    assert Config().enemy_kill_switch is False and Config().aimd is False
