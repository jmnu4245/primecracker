import logging
import psutil
import json
import time
import os

log = logging.getLogger(__name__)

os.makedirs("runtime", exist_ok=True)

# Drivers de temperatura de CPU por orden de preferencia: Intel, AMD (dos
# variantes), ARM, y como último recurso la zona térmica ACPI.
CPU_SENSORS = ("coretemp", "k10temp", "zenpower", "cpu_thermal", "acpitz")


def cpu_temperature(sensors):
    """Temperatura más alta del primer driver de CPU disponible, o None.

    Antes se tomaba la primera lectura de cualquier sensor: en este equipo
    era el SSD NVMe (~32 °C) con la CPU a 69 °C, así que la regla de
    temperatura crítica del controlador no podía saltar nunca."""
    for driver in CPU_SENSORS:
        readings = [e.current for e in sensors.get(driver, []) if e.current is not None]
        if readings:
            return max(readings)
    return None


def get_temperature():
    if not hasattr(psutil, "sensors_temperatures"):
        return None  # p.ej. Windows: psutil no lee temperatura
    return cpu_temperature(psutil.sensors_temperatures())


def main():
    log.info("[*] Iniciando Resource Monitor...")
    if get_temperature() is None:
        log.warning("[!] No se encontró sensor de temperatura de CPU: la regla de "
              "temperatura crítica del controlador queda desactivada.")
    while True:
        data = {
            "cpu_percent": psutil.cpu_percent(interval=1),
            "ram_percent": psutil.virtual_memory().percent,
            "temperature": get_temperature(),
            "last_updated": time.time()
        }
        tmp_file = "runtime/resources.json.tmp"
        final_file = "runtime/resources.json"
        
        with open(tmp_file, "w") as f:
            json.dump(data, f, indent=2)
        os.replace(tmp_file, final_file)
        
        time.sleep(2)

if __name__ == "__main__":
    import log_setup
    log_setup.install_service()
    main()