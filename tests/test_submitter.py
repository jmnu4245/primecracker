"""
Tests de submitter/auto_submit.py: validación de factores, entrega única
por N, y que procese los archivos que llegan por rename atómico y los que
ya existían al arrancar.
"""

import json
from types import SimpleNamespace

import pytest

import submitter.auto_submit as auto_submit
from submitter.auto_submit import FactorSubmitter, verify_factors


@pytest.mark.parametrize("n,factors,ok", [
    ("10403", ["101", "103"], True),
    ("30", ["2", "3", "5"], True),
    ("10403", ["10403", "1"], False),   # lo que ECM daba al "encontrar" N entero
    ("10403", ["10403"], False),
    ("10403", ["101", "101"], False),
    ("10403", ["-101", "-103"], False),
    ("10403", ["abc", "103"], False),
    (None, ["101", "103"], False),
])
def test_verify_factors(n, factors, ok):
    assert verify_factors(n, factors) is ok


@pytest.fixture
def runtime(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "runtime").mkdir()
    sent = []
    monkeypatch.setattr(auto_submit, "send_email_simulated", sent.append)

    def drop(name, n, factors, method="test"):
        path = tmp_path / "runtime" / name
        path.write_text(json.dumps({"n": str(n), "factors": [str(f) for f in factors], "method": method}))
        return str(path)
    return tmp_path, drop, sent

def history(tmp_path):
    path = tmp_path / "runtime" / "solved_history.jsonl"
    return [json.loads(l) for l in path.read_text().splitlines()] if path.exists() else []


def test_entrega_y_borra(runtime):
    tmp_path, drop, sent = runtime
    path = drop("factor_found_a.json", 10403, [101, 103])
    FactorSubmitter().process(path)
    assert len(sent) == 1
    assert [h["n"] for h in history(tmp_path)] == ["10403"]
    assert not (tmp_path / "runtime" / "factor_found_a.json").exists()

def test_mismo_n_de_dos_workers_se_entrega_una_vez(runtime):
    tmp_path, drop, sent = runtime
    sub = FactorSubmitter()
    sub.process(drop("factor_found_ecm.json", 10403, [101, 103], "GMP-ECM"))
    sub.process(drop("factor_found_gnfs.json", 10403, [103, 101], "CADO-NFS"))
    assert len(sent) == 1
    assert len(history(tmp_path)) == 1

def test_no_reentrega_lo_del_historial_de_otra_corrida(runtime):
    tmp_path, drop, sent = runtime
    (tmp_path / "runtime" / "solved_history.jsonl").write_text(json.dumps({"n": "10403"}) + "\n")
    FactorSubmitter().process(drop("factor_found_a.json", 10403, [101, 103]))
    assert sent == []

def test_factorizacion_trivial_no_se_entrega(runtime):
    tmp_path, drop, sent = runtime
    FactorSubmitter().process(drop("factor_found_a.json", 10403, [10403, 1]))
    assert sent == []
    assert history(tmp_path) == []

def test_on_moved_procesa_el_destino(runtime):
    # Los workers hacen os.replace(tmp, final): llega como "moved".
    tmp_path, drop, sent = runtime
    final = drop("factor_found_a.json", 10403, [101, 103])
    FactorSubmitter().on_moved(SimpleNamespace(is_directory=False,
                                               src_path=final + ".tmp", dest_path=final))
    assert len(sent) == 1

def test_process_existing_recoge_lo_escrito_antes_de_arrancar(runtime):
    tmp_path, drop, sent = runtime
    drop("factor_found_a.json", 10403, [101, 103])
    drop("factor_found_b.json", 15, [3, 5])
    drop("otro_archivo.json", 21, [3, 7])
    FactorSubmitter().process_existing(str(tmp_path / "runtime"))
    assert sorted(h["n"] for h in history(tmp_path)) == ["10403", "15"]

def test_ignora_tmp_y_json_corrupto(runtime):
    tmp_path, drop, sent = runtime
    sub = FactorSubmitter()
    sub.process(drop("factor_found_a.json.tmp", 10403, [101, 103]))
    bad = tmp_path / "runtime" / "factor_found_b.json"
    bad.write_text("{a medio")
    sub.process(str(bad))
    assert sent == []
