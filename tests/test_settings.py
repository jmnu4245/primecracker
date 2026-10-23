import pytest

import settings


def test_sin_archivo_valores_por_defecto(tmp_path):
    assert settings.load(str(tmp_path / "no.toml")) == settings.Settings()

def test_el_archivo_del_repo_es_valido():
    assert settings.load() == settings.Settings()    # todo comentado: defaults

def test_sobrescribe_lo_indicado(tmp_path):
    path = tmp_path / "s.toml"
    path.write_text('gnfs_min_digits = 100\ntoolchain_dir = "/opt/tc"\n')
    s = settings.load(str(path))
    assert s.gnfs_min_digits == 100
    assert s.binary("ecm_bin") == "/opt/tc/ecm_bin"
    assert s.budget_margin == settings.Settings().budget_margin

def test_clave_desconocida_es_error(tmp_path):
    path = tmp_path / "s.toml"
    path.write_text("gnfs_min_digit = 100\n")          # errata
    with pytest.raises(ValueError, match="gnfs_min_digit"):
        settings.load(str(path))

def test_hilos_automaticos(monkeypatch):
    monkeypatch.setattr(settings.os, "cpu_count", lambda: 12)
    assert settings.Settings().threads() == (12, 6, 2)
    monkeypatch.setattr(settings.os, "cpu_count", lambda: 2)
    assert settings.Settings().threads() == (2, 1, 1)
    assert settings.Settings(anchor_threads=3).threads() == (2, 3, 1)
