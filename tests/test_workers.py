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


# ----------------------- GNFS: proyección de fin -----------------------

from workers.gnfs_worker import parse_eta_line, project_finish_at, POST_SIEVE_FACTOR

def test_parse_eta_line_con_ansi_y_dia_de_un_digito():
    line = ("\x1b[32;1mInfo\x1b[0m:Lattice Sieving: Marking workunit c100_sieving_1000-2000 "
            "as ok (35.2% => ETA Thu Oct  3 01:23:45 2026)")
    phase, pct, eta = parse_eta_line(line)
    assert (phase, pct) == ("Lattice Sieving", 35.2)
    assert (eta.day, eta.hour, eta.minute) == (3, 1, 23)

def test_parse_eta_line_ignora_otras_lineas():
    assert parse_eta_line("Info:Lattice Sieving: param rels_wanted is 3600000") is None
    assert parse_eta_line("Info:Square Root: Factors: 101 103") is None

def test_project_finish_at_suma_la_cola_tras_la_criba():
    sieve_start = 1000.0
    eta = sieve_start + 1200                  # 20 min de criba
    assert project_finish_at(eta, sieve_start) == pytest.approx(eta + POST_SIEVE_FACTOR * 1200)

def test_project_finish_at_eta_anterior_al_inicio_no_resta():
    assert project_finish_at(500.0, 1000.0) == 500.0


# ----------------------- YAFU con binario falso -----------------------

FAKE_YAFU = r'''#!/bin/bash
read expr
n=$(echo "$expr" | tr -dc '0-9')
# Más de 64 KB de salida: con el pipe sin leer, YAFU se bloqueaba aquí.
head -c 200000 /dev/zero | tr '\0' 'x'; echo
printf '{"input-decimal":"%s","factors-prime":["101","103"]}' "$n" > factor.json
'''

def test_yafu_no_se_bloquea_con_mucha_salida_y_usa_su_directorio(in_tmp, monkeypatch):
    path = in_tmp / "fake_yafu"
    path.write_text(FAKE_YAFU)
    path.chmod(0o755)
    monkeypatch.setattr(YAFUWorker, "BINARY", str(path))
    w = YAFUWorker()
    w.start(10403, threads=2, timeout=30)
    assert w.found.wait(10)
    assert w.result == [101, 103]
    assert not (in_tmp / "factor.json").exists()        # nada en el directorio actual
    workdir = w.workdir
    assert os.path.exists(os.path.join(workdir, "factor.json"))
    w.discard_workdir()
    assert not os.path.exists(workdir)

def test_yafu_borra_siqs_dat_al_reiniciar(in_tmp, monkeypatch):
    path = in_tmp / "fake_yafu"
    path.write_text("#!/bin/bash\nsleep 30\n")
    path.chmod(0o755)
    monkeypatch.setattr(YAFUWorker, "BINARY", str(path))
    w = YAFUWorker()
    w.start(10403, threads=2, timeout=60)
    (open(os.path.join(w.workdir, "siqs.dat"), "w")).write("truncado")
    w.MIN_RESIZE_INTERVAL = 0
    w.resize(4)
    assert not os.path.exists(os.path.join(w.workdir, "siqs.dat"))
    w.discard_workdir()


# ----------------------- GNFS: clientes demonizados de CADO -----------------------

def test_gnfs_kill_mata_clientes_demonizados_solo_de_su_workdir(in_tmp):
    from workers.gnfs_worker import GNFSWorker
    workdir = str(in_tmp / "cado_wd")
    client = in_tmp / "cado-nfs-client.py"
    client.write_text("import time\ntime.sleep(60)\n")
    import sys
    # Como CADO: sesión propia (fuera del grupo de proceso del worker).
    daemon = subprocess.Popen([sys.executable, str(client), "--basepath", workdir + "/client"],
                              start_new_session=True)
    other = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)", workdir],
                             start_new_session=True)
    try:
        w = GNFSWorker()
        w.workdir = workdir
        w.kill()                       # sin proceso propio: solo limpia clientes
        assert daemon.wait(timeout=5) is not None
        assert other.poll() is None    # menciona la ruta pero no es cliente de CADO
    finally:
        for p in (daemon, other):
            p.kill()
            p.wait()


def test_busy_cubre_el_hueco_entre_kill_y_found(in_tmp):
    import threading
    w = BaseWorker("t", "Test")
    assert not w.busy()
    # Simula _write_success a medias: proceso ya muerto, monitor descomponiendo.
    w.thread = threading.Thread(target=time.sleep, args=(1,))
    w.thread.start()
    assert not w.is_running() and w.busy()
    w.thread.join()
    assert not w.busy()
