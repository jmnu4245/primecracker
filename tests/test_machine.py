import json

import pytest

from benchmark import machine


def specs(cores, threads, mhz, model="CPU de prueba"):
    return {"cpu_model": model, "physical_cores": cores, "logical_cpus": threads, "max_mhz": mhz}


def test_misma_maquina_factor_1():
    assert machine.factor_from_specs(dict(machine.REFERENCE)) == pytest.approx(1.0)

def test_doble_de_nucleos_mitad_de_tiempo():
    ref = machine.REFERENCE
    doble = specs(ref["physical_cores"] * 2, ref["logical_cpus"] * 2, ref["max_mhz"])
    assert machine.factor_from_specs(doble) == pytest.approx(0.5)

def test_smt_cuenta_poco():
    # 8 núcleos sin SMT rinden más que 4 núcleos / 8 hilos
    assert machine.capacity(specs(8, 8, 3000)) > machine.capacity(specs(4, 8, 3000))

def test_cache_se_invalida_si_cambia_la_maquina_o_el_turbo(tmp_path, monkeypatch):
    path = tmp_path / "machine.json"
    current = specs(6, 12, 2600.0)
    monkeypatch.setattr(machine, "current_specs", lambda: dict(current))
    path.write_text(json.dumps({**current, "factor": 0.8}))
    assert machine.load_factor(str(path)) == 0.8
    current["max_mhz"] = 4500.0                      # se activó el turbo
    assert machine.load_factor(str(path)) is None

def test_sin_calibracion_usa_especificaciones(tmp_path, monkeypatch):
    path = tmp_path / "machine.json"
    monkeypatch.setattr(machine, "current_specs", lambda: specs(12, 24, 2600.0))
    monkeypatch.setattr(machine, "calibrate", lambda: None)   # p.ej. sin YAFU
    factor = machine.ensure_factor(str(path))
    assert factor == pytest.approx(0.5)
    saved = json.loads(path.read_text())
    assert saved["method"] == "núcleos × GHz" and saved["factor"] == factor
