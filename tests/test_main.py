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
    assert isinstance(main.make_anchor_worker(10 ** (main.SETTINGS.gnfs_min_digits - 1)), GNFSWorker)


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


def test_log_setup_consola_igual_que_antes_y_archivo_con_nivel(tmp_path, capsys):
    import logging
    import log_setup
    path = tmp_path / "runtime" / "test.log"
    root = logging.getLogger()
    saved = root.handlers[:], root.level
    try:
        log_setup.install(str(path), console_level=logging.WARNING)
        logging.getLogger("workers.x").info("solo al archivo")
        logging.getLogger("workers.x").warning("[!] a los dos")
        out = capsys.readouterr().out
        assert out == "[!] a los dos\n"            # consola: solo el mensaje, filtrado
        lines = path.read_text().splitlines()
        assert lines[-2].endswith("INFO    workers.x: solo al archivo")
        assert lines[-1].endswith("WARNING workers.x: [!] a los dos")
    finally:
        root.handlers, root.level = saved


def test_servicio_conserva_el_nivel_en_mensajes_de_varias_lineas():
    import logging
    import log_setup
    record = logging.LogRecord("s", logging.WARNING, __file__, 1, "linea 1\nlinea 2", None, None)
    text = log_setup._ServiceFormatter("%(message)s").format(record)
    assert [log_setup.parse_service_line(l) for l in text.splitlines()] == [
        (logging.WARNING, "linea 1"), (logging.WARNING, "linea 2")]


def test_linea_de_servicio_sin_nivel_cuenta_como_error():
    import logging
    import log_setup
    assert log_setup.parse_service_line("Traceback (most recent call last):") == (
        logging.ERROR, "Traceback (most recent call last):")
    assert log_setup.parse_service_line("INFO|a|b") == (logging.INFO, "a|b")


# ----------------------- reparto de tiempo -----------------------

import pytest

def fixed_predictor(seconds):
    return lambda digits, method: (seconds, 100.0)

def failing_predictor(digits, method):
    raise ValueError("sin datos")

def test_plan_budget_escala_por_hilos_y_aplica_margen(monkeypatch):
    monkeypatch.setattr(main.os, "cpu_count", lambda: 12)
    budget, estimate = main.plan_budget(10 ** 80, 3600, fixed_predictor(100), anchor_threads=6)
    assert estimate == 200                         # 100s con 12 hilos -> 200s con 6
    assert budget == 200 * main.SETTINGS.budget_margin

def test_plan_budget_pospone_si_no_cabe(monkeypatch):
    monkeypatch.setattr(main.os, "cpu_count", lambda: 6)
    budget, estimate = main.plan_budget(10 ** 80, 600, fixed_predictor(1000), anchor_threads=6)
    assert budget is None and estimate == 1000

def test_plan_budget_minimo(monkeypatch):
    monkeypatch.setattr(main.os, "cpu_count", lambda: 6)
    budget, _ = main.plan_budget(10 ** 80, 3600, fixed_predictor(1), anchor_threads=6)
    assert budget == main.SETTINGS.min_target_seconds

def test_plan_budget_sin_prediccion_usa_reparto_fijo():
    assert main.plan_budget(10 ** 80, 3600, failing_predictor, 6) == (main.SETTINGS.default_target_seconds, None)
    assert main.plan_budget(10 ** 80, 100, failing_predictor, 6) == (100, None)

def test_target_config_ajusta_el_plazo_del_controlador():
    from orchestrator.controller import Config
    cfg = main.target_config(Config(), 600)
    assert cfg.deadline_minutes == 10
    assert cfg.variance_abort_minute == pytest.approx(3)
    assert cfg.finish_margin_minutes == pytest.approx(1)

def test_resolve_total_seconds(tmp_path):
    state = tmp_path / "state.json"
    assert main.resolve_total_seconds(5, str(state)) == 300
    assert main.resolve_total_seconds(None, str(state)) == main.SETTINGS.total_minutes * 60
    state.write_text(json.dumps({"time_remaining_seconds": 1234}))
    assert main.resolve_total_seconds(None, str(state)) == pytest.approx(1234, abs=2)

def test_resolve_total_seconds_descuenta_la_antiguedad_de_state_json(tmp_path):
    import os, time
    state = tmp_path / "state.json"
    state.write_text(json.dumps({"time_remaining_seconds": 3600}))
    old = time.time() - 600                 # escrito hace 10 minutos
    os.utime(state, (old, old))
    assert main.resolve_total_seconds(None, str(state)) == pytest.approx(3000, abs=2)
    os.utime(state, (old - 7200, old - 7200))
    assert main.resolve_total_seconds(None, str(state)) == 0     # ya terminó


# ----------------------- sub-objetivos (factores compuestos) -----------------------

class _Fake:
    method_name = "Fake"
    def kill(self): pass

@pytest.fixture
def fake_pipeline(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "runtime").mkdir()
    import benchmark.predict_cost, workers.ecm_worker
    # Estimación holgada: el ataque falso es instantáneo y debe sobrar
    # presupuesto para los sub-objetivos dentro de la misma pasada.
    monkeypatch.setattr(benchmark.predict_cost, "predict", lambda d, method: (100.0, 1.0))
    monkeypatch.setattr(workers.ecm_worker, "ECMWorker", _Fake)
    monkeypatch.setattr(main, "make_anchor_worker", lambda t: _Fake())
    calls = []
    def install(answers):
        def fake_attack(target, cfg, anchor, ecm, loop, budget):
            calls.append(target)
            return answers.get(target)
        monkeypatch.setattr(main, "attack_target", fake_attack)
    return tmp_path, install, calls

def written(tmp_path):
    return {json.loads(p.read_text())["n"]: json.loads(p.read_text())
            for p in (tmp_path / "runtime").glob("factor_found_*.json")}

def test_compuesto_se_resuelve_como_sub_objetivo(fake_pipeline):
    tmp_path, install, calls = fake_pipeline
    install({30: [2, 15], 15: [3, 5]})
    main.run_control_loop([30], total_seconds=600)
    assert calls == [30, 15]
    result = written(tmp_path)["30"]
    assert result["factors"] == ["2", "3", "5"] and result["complete"] is True

def test_lo_parcial_se_conserva_para_la_segunda_pasada(fake_pipeline):
    tmp_path, install, calls = fake_pipeline
    install({30: [2, 15]})          # el sub-objetivo 15 no sale en ninguna pasada
    main.run_control_loop([30], total_seconds=600)
    # 30 se ataca una sola vez; la segunda pasada reintenta solo el 15
    assert calls == [30, 15, 15]
    assert "30" not in written(tmp_path)


def test_plan_budget_suma_el_pretest_ecm_ante_gnfs(monkeypatch):
    monkeypatch.setattr(main.os, "cpu_count", lambda: 12)
    _, estimate = main.plan_budget(10 ** 100, 10 ** 6, fixed_predictor(1000), anchor_threads=12)
    assert estimate == 1000 + main.SETTINGS.ecm_pretest_seconds
    _, estimate = main.plan_budget(10 ** 80, 10 ** 6, fixed_predictor(1000), anchor_threads=12)
    assert estimate == 1000                                     # YAFU: sin pretest


def test_flush_registra_lo_escrito_justo_antes_del_apagado(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "runtime").mkdir()
    (tmp_path / "data").mkdir()
    (tmp_path / "data" / "target_numbers.txt").write_text("30\n")
    (tmp_path / "runtime" / "factor_found_cleanup_30.json").write_text(
        json.dumps({"n": "30", "factors": ["2", "3", "5"], "method": "Combinado"}))
    main.flush_pending_results()
    assert "N (2 dígitos) = 30" in (tmp_path / "runtime" / "para_entregar.txt").read_text()
