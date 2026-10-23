from types import SimpleNamespace as S

from orchestrator.resource_monitor import cpu_temperature


def test_prefiere_la_cpu_aunque_otro_sensor_vaya_antes():
    # Orden real de este equipo: el NVMe aparece primero.
    sensors = {"nvme": [S(current=31.9)],
               "coretemp": [S(current=69.0), S(current=67.0), S(current=72.0)]}
    assert cpu_temperature(sensors) == 72.0

def test_amd():
    assert cpu_temperature({"k10temp": [S(current=81.5)]}) == 81.5

def test_acpi_como_ultimo_recurso():
    assert cpu_temperature({"nvme": [S(current=30)], "acpitz": [S(current=55)]}) == 55

def test_sin_sensor_de_cpu_es_desconocido_no_cero():
    assert cpu_temperature({"nvme": [S(current=31.9)]}) is None
    assert cpu_temperature({}) is None
