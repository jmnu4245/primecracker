"""
Tests de submitter/collector.py: validación de factores, registro único
por N, y que procese los archivos que llegan por rename atómico y los que
ya existían al arrancar.
"""

import json
from types import SimpleNamespace

import pytest

from submitter.collector import FactorCollector, verify_factors


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


class Recorded:
    """Lo anotado en runtime/para_entregar.txt, como lista de payloads
    (n y factores) para poder compararlo igual que antes los envíos."""
    def __init__(self, tmp_path):
        self.path = tmp_path / "runtime" / "para_entregar.txt"

    def _items(self):
        if not self.path.exists():
            return []
        lines = self.path.read_text().splitlines()
        items = []
        for head, body in zip(lines[::2], lines[1::2]):
            n = head.split("= ")[1]
            factors = body.split("factores: ")[1].split("    [")[0].split(" * ")
            items.append({"n": n, "factors": factors})
        return items

    def __iter__(self): return iter(self._items())
    def __len__(self): return len(self._items())
    def __eq__(self, other): return self._items() == other


@pytest.fixture
def runtime(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "runtime").mkdir()
    sent = Recorded(tmp_path)

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
    FactorCollector().process(path)
    assert len(sent) == 1
    assert [h["n"] for h in history(tmp_path)] == ["10403"]
    assert not (tmp_path / "runtime" / "factor_found_a.json").exists()

def test_mismo_n_de_dos_workers_se_entrega_una_vez(runtime):
    tmp_path, drop, sent = runtime
    sub = FactorCollector()
    sub.process(drop("factor_found_ecm.json", 10403, [101, 103], "GMP-ECM"))
    sub.process(drop("factor_found_gnfs.json", 10403, [103, 101], "CADO-NFS"))
    assert len(sent) == 1
    assert len(history(tmp_path)) == 1

def test_no_reentrega_lo_del_historial_de_otra_corrida(runtime):
    tmp_path, drop, sent = runtime
    (tmp_path / "runtime" / "solved_history.jsonl").write_text(json.dumps({"n": "10403"}) + "\n")
    FactorCollector().process(drop("factor_found_a.json", 10403, [101, 103]))
    assert sent == []

def test_factorizacion_trivial_no_se_entrega(runtime):
    tmp_path, drop, sent = runtime
    FactorCollector().process(drop("factor_found_a.json", 10403, [10403, 1]))
    assert sent == []
    assert history(tmp_path) == []

def test_on_moved_procesa_el_destino(runtime):
    # Los workers hacen os.replace(tmp, final): llega como "moved".
    tmp_path, drop, sent = runtime
    final = drop("factor_found_a.json", 10403, [101, 103])
    FactorCollector().on_moved(SimpleNamespace(is_directory=False,
                                               src_path=final + ".tmp", dest_path=final))
    assert len(sent) == 1

def test_process_existing_recoge_lo_escrito_antes_de_arrancar(runtime):
    tmp_path, drop, sent = runtime
    drop("factor_found_a.json", 10403, [101, 103])
    drop("factor_found_b.json", 15, [3, 5])
    drop("otro_archivo.json", 21, [3, 7])
    FactorCollector().process_existing(str(tmp_path / "runtime"))
    assert sorted(h["n"] for h in history(tmp_path)) == ["10403", "15"]

def test_ignora_tmp_y_json_corrupto(runtime):
    tmp_path, drop, sent = runtime
    sub = FactorCollector()
    sub.process(drop("factor_found_a.json.tmp", 10403, [101, 103]))
    bad = tmp_path / "runtime" / "factor_found_b.json"
    bad.write_text("{a medio")
    sub.process(str(bad))
    assert sent == []


# ----------------------- solo objetivos actuales -----------------------

def write_targets(tmp_path, *numbers):
    (tmp_path / "data").mkdir(exist_ok=True)
    (tmp_path / "data" / "target_numbers.txt").write_text(
        "# comentario\n" + "".join(f"{n}\n" for n in numbers))

def test_no_entrega_lo_que_no_es_objetivo_y_lo_aparta(runtime):
    tmp_path, drop, sent = runtime
    write_targets(tmp_path, 10403)
    sub = FactorCollector()
    sub.process(drop("factor_found_viejo.json", 15, [3, 5]))
    sub.process(drop("factor_found_bueno.json", 10403, [101, 103]))
    assert [p["n"] for p in sent] == ["10403"]
    assert (tmp_path / "runtime" / "ignored" / "factor_found_viejo.json").exists()

def test_recarga_objetivos_anadidos_durante_la_corrida(runtime):
    tmp_path, drop, sent = runtime
    write_targets(tmp_path, 10403)
    sub = FactorCollector()
    write_targets(tmp_path, 10403, 15)        # reto nuevo publicado a mitad
    sub.process(drop("factor_found_a.json", 15, [3, 5]))
    assert [p["n"] for p in sent] == ["15"]


def test_factorizacion_parcial_no_se_entrega_y_se_aparta(runtime):
    tmp_path, drop, sent = runtime
    # 30 = 2 * 15, con 15 compuesto
    FactorCollector().process(drop("factor_found_a.json", 30, [2, 15]))
    assert sent == []
    assert (tmp_path / "runtime" / "partial" / "factor_found_a.json").exists()

def test_la_completa_posterior_si_se_entrega(runtime):
    tmp_path, drop, sent = runtime
    sub = FactorCollector()
    sub.process(drop("factor_found_a.json", 30, [2, 15]))
    sub.process(drop("factor_found_b.json", 30, [2, 3, 5]))
    assert [p["factors"] for p in sent] == [["2", "3", "5"]]


def test_linea_de_entrega_legible(runtime):
    tmp_path, drop, sent = runtime
    FactorCollector().process(drop("factor_found_a.json", 10403, [103, 101], "YAFU"))
    text = (tmp_path / "runtime" / "para_entregar.txt").read_text()
    assert "N (5 dígitos) = 10403" in text
    assert "factores: 101 * 103    [YAFU]" in text


def test_parcial_tardio_de_un_numero_ya_registrado_es_duplicado(runtime):
    tmp_path, drop, sent = runtime
    sub = FactorCollector()
    sub.process(drop("factor_found_yafu.json", 30, [2, 3, 5]))
    sub.process(drop("factor_found_ecm.json", 30, [2, 15]))      # llega después
    assert len(sent) == 1
    assert not (tmp_path / "runtime" / "partial").exists()
