"""
Tests de workers/: validación de resultados, guarda de generación, kill()
del grupo de procesos y paralelismo de ECM con un binario falso. Los tests
marcados `integration` usan los binarios reales de bin/active y se saltan
si no están.
"""

import json
import os
import stat
import subprocess
import time

import pytest

from workers.base_worker import BaseWorker
from workers.ecm_worker import ECMWorker
from workers.yafu_worker import YAFUWorker

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
REAL_ECM = os.path.join(ROOT, "bin/active/ecm_bin")
REAL_YAFU = os.path.join(ROOT, "bin/active/yafu_bin")


@pytest.fixture(autouse=True)
def in_tmp(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    return tmp_path


def fake_binary(tmp_path, body):
    path = tmp_path / "fake_ecm"
    path.write_text("#!/bin/bash\n" + body)
    path.chmod(path.stat().st_mode | stat.S_IEXEC)
    return str(path)


def found_files(tmp_path):
    return [json.loads(p.read_text()) for p in (tmp_path / "runtime").glob("factor_found_*.json")]


# ----------------------- BaseWorker._write_success -----------------------

def test_write_success_acepta_factorizacion_valida(in_tmp):
    w = BaseWorker("t", "Test")
    assert w._write_success(10403, [101, 103])
    assert w.found.is_set() and w.result == [101, 103]
    assert found_files(in_tmp)[0]["factors"] == ["101", "103"]

@pytest.mark.parametrize("factors", [[10403, 1], [10403], [101, 101], [0, 0]])
def test_write_success_rechaza_trivial_o_incorrecta(in_tmp, factors):
    w = BaseWorker("t", "Test")
    assert not w._write_success(10403, factors)
    assert not w.found.is_set()
    assert found_files(in_tmp) == []

def test_write_success_ignora_generacion_vieja(in_tmp):
    w = BaseWorker("t", "Test")
    w._generation = 2
    assert not w._write_success(10403, [101, 103], generation=1)
    assert w._write_success(10403, [101, 103], generation=2)


# ----------------------- kill() / ciclo de vida -----------------------

def test_kill_mata_todo_el_grupo_y_recoge_el_proceso(in_tmp):
    w = BaseWorker("t", "Test")
    w._start_process(["bash", "-c", "sleep 60 & sleep 60 & wait"], timeout=60)
    time.sleep(0.3)
    pgid = os.getpgid(w.process.pid)
    w.kill()
    assert not w.is_running()            # inmediatamente, sin esperar
    time.sleep(0.2)
    alive = subprocess.run(["pgrep", "-g", str(pgid)], capture_output=True, text=True).stdout
    assert alive.strip() == ""

def test_watchdog_mata_por_timeout(in_tmp):
    w = BaseWorker("t", "Test")
    w._start_process(["sleep", "60"], timeout=1)
    time.sleep(3)
    assert not w.is_running()

def test_resize_respeta_cooldown(in_tmp):
    restarts = []
    class W(BaseWorker):
        MIN_RESIZE_INTERVAL = 60
        def _restart_with(self, threads):
            restarts.append(threads)
            self.current_threads = threads
            self._start_process(["sleep", "60"], timeout=60)
    w = W("t", "Test")
    w.current_n, w.current_threads = 10403, 2
    w._start_process(["sleep", "60"], timeout=60)
    w.resize(3)
    w.resize(4)          # dentro del cooldown: ignorado
    w.kill()
    assert restarts == [3]


# ----------------------- ECMWorker con binario falso -----------------------

def test_ecm_lanza_un_proceso_por_hilo(in_tmp, monkeypatch):
    marker = in_tmp / "launches"
    binary = fake_binary(in_tmp, f'echo "$@" >> {marker}\ncat >/dev/null\nsleep 30\n')
    monkeypatch.setattr(ECMWorker, "BINARY", binary)
    w = ECMWorker()
    w.start(10403, threads=4, b1=1000, timeout=60)
    time.sleep(1)
    w.kill()
    launches = marker.read_text().splitlines()
    assert len(launches) == 4
    # 50 curvas repartidas entre 4 procesos -> 13 cada uno, con -one
    assert all(l.split() == ["-one", "-c", "13", "1000"] for l in launches)

def test_ecm_ignora_factor_igual_a_n_y_toma_el_siguiente(in_tmp, monkeypatch):
    binary = fake_binary(in_tmp, 'read n\n'
                                 'echo "********** Factor found in step 1: $n"\n'
                                 'echo "********** Factor found in step 2: 101"\n'
                                 'sleep 30\n')
    monkeypatch.setattr(ECMWorker, "BINARY", binary)
    w = ECMWorker()
    w.start(10403, threads=1, b1=1000, timeout=60)
    assert w.found.wait(10)
    assert w.result == [101, 103]
    assert not w.is_running()

def test_ecm_solo_n_entero_no_da_exito(in_tmp, monkeypatch):
    binary = fake_binary(in_tmp, 'read n\necho "********** Factor found in step 1: $n"\n')
    monkeypatch.setattr(ECMWorker, "BINARY", binary)
    w = ECMWorker()
    w.start(10403, threads=2, b1=1000, timeout=60)
    w.process.wait(10)
    time.sleep(0.3)
    assert not w.found.is_set()
    assert found_files(in_tmp) == []


# ----------------------- binarios reales -----------------------

@pytest.mark.integration
@pytest.mark.skipif(not os.path.exists(REAL_ECM), reason="sin bin/active/ecm_bin")
def test_ecm_real_encuentra_factor_pequeno(monkeypatch):
    monkeypatch.setattr(ECMWorker, "BINARY", REAL_ECM)
    n = 1000003 * (2**61 - 1)
    w = ECMWorker()
    w.start(n, threads=2, b1=10000, timeout=60)
    assert w.found.wait(60)
    assert sorted(w.result) == [1000003, 2**61 - 1]

@pytest.mark.integration
@pytest.mark.skipif(not os.path.exists(REAL_YAFU), reason="sin bin/active/yafu_bin")
def test_yafu_real_sin_terminal(monkeypatch):
    # Sin TTY en stdin: antes YAFU ignoraba el número y no factorizaba nada.
    monkeypatch.setattr(YAFUWorker, "BINARY", REAL_YAFU)
    w = YAFUWorker()
    w.start(20000000000000000900000000000000009, threads=2, timeout=60)
    assert w.found.wait(60)
    assert sorted(w.result) == [100000000000000003, 200000000000000003]
