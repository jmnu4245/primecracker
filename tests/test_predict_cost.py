import csv
import math

import pytest

import benchmark.predict_cost as pc


def write_csv(path, method, t0, a, c, sizes):
    alpha = pc.ALPHA_BY_METHOD[pc.canonical_method(method)]
    with open(path, "a", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["digits", "method", "success", "elapsed_seconds",
                                          "timed_out", "peak_ram_mb"])
        if f.tell() == 0:
            w.writeheader()
        for d in sizes:
            t = t0 + math.exp(a + c * float(pc._l_shape(d, alpha)))
            w.writerow({"digits": d, "method": method, "success": "True",
                        "elapsed_seconds": t, "timed_out": "False", "peak_ram_mb": 100})


@pytest.fixture(autouse=True)
def no_cache(monkeypatch):
    monkeypatch.setattr(pc, "_MODEL_CACHE", {})


def test_recupera_coste_fijo_y_crecimiento(tmp_path):
    path = tmp_path / "b.csv"
    write_csv(path, "YAFU", t0=1.64, a=-16.71, c=0.656, sizes=range(20, 95, 5))
    m = pc.fit_method(pc.load_results(str(path)), "YAFU")
    assert m["t0"] == pytest.approx(1.64, rel=0.05)
    # Extrapola bien fuera de los datos (el modelo antiguo, sin t0, no).
    expected = 1.64 + math.exp(-16.71 + 0.656 * float(pc._l_shape(100, 0.5)))
    assert pc.predict(100, "YAFU", str(path))[0] == pytest.approx(expected, rel=0.05)


def test_alias_de_metodo(tmp_path):
    path = tmp_path / "b.csv"
    write_csv(path, "CADO-NFS", t0=25.9, a=-15.94, c=1.19, sizes=range(60, 95, 5))
    assert pc.predict(100, "GNFS", str(path)) == pc.predict(100, "CADO-NFS", str(path))


def test_pocos_tamanos_es_error(tmp_path):
    path = tmp_path / "b.csv"
    write_csv(path, "YAFU", t0=1.64, a=-16.71, c=0.656, sizes=[60, 60, 70, 80])
    with pytest.raises(ValueError, match="al menos 4"):
        pc.predict(90, "YAFU", str(path))


def test_crossover(tmp_path):
    # Parámetros del ajuste a las mediciones reales de este equipo.
    path = tmp_path / "b.csv"
    write_csv(path, "YAFU", t0=1.64, a=-16.71, c=0.656, sizes=range(40, 100, 5))
    write_csv(path, "CADO-NFS", t0=25.9, a=-15.94, c=1.19, sizes=range(60, 100, 5))
    x = pc.crossover_digits(csv_path=str(path))
    assert x is not None
    assert pc.predict(x, "CADO-NFS", str(path))[0] < pc.predict(x, "YAFU", str(path))[0]
    assert pc.predict(x - 1, "CADO-NFS", str(path))[0] >= pc.predict(x - 1, "YAFU", str(path))[0]
