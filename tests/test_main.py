"""Tests de las piezas puras de main.py: reanudación y elección de método."""

import json

import main
from workers.gnfs_worker import GNFSWorker
from workers.yafu_worker import YAFUWorker


def test_load_solved_history_tolera_lineas_corruptas(tmp_path):
    path = tmp_path / "solved_history.jsonl"
    path.write_text(
        json.dumps({"n": "10403", "factors": ["101", "103"]}) + "\n"
        + '{"n": "15", "factors": ["3", "5"]\n'          # cortada a medias
        + json.dumps({"sin_n": 1}) + "\n"
        + json.dumps({"n": "21"}) + "\n"
    )
    assert main.load_solved_history(str(path)) == {10403, 21}

def test_load_solved_history_sin_archivo(tmp_path):
    assert main.load_solved_history(str(tmp_path / "no_existe.jsonl")) == set()

def test_anchor_yafu_para_numeros_pequenos(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    assert isinstance(main.make_anchor_worker(10 ** 80), YAFUWorker)

def test_anchor_gnfs_desde_el_umbral(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    assert isinstance(main.make_anchor_worker(10 ** (main.GNFS_MIN_DIGITS - 1)), GNFSWorker)


def test_restart_dead_services_relanza_solo_los_caidos(monkeypatch):
    class Proc:
        def __init__(self, code): self.returncode = code
        def poll(self): return self.returncode
    launched = []
    monkeypatch.setattr(main, "_launch_service", lambda s: launched.append(s) or Proc(None))
    services = {"vivo.py": Proc(None), "caido.py": Proc(1)}
    main.restart_dead_services(services)
    assert launched == ["caido.py"]
    assert services["caido.py"].returncode is None


def test_log_setup_escribe_lineas_completas_con_marca_de_tiempo(tmp_path, monkeypatch):
    import sys
    import log_setup
    path = tmp_path / "runtime" / "test.log"
    monkeypatch.setattr(sys, "stdout", sys.stdout)
    monkeypatch.setattr(sys, "stderr", sys.stderr)
    log_setup.install(str(path))
    sys.stdout.write("hola ")
    sys.stdout.write("mundo\nsegunda\n")
    sys.stdout.flush()
    lines = path.read_text().splitlines()
    assert lines[-2].endswith(" hola mundo")
    assert lines[-1].endswith(" segunda")
    assert lines[-1][:4].isdigit()          # empieza por el año
