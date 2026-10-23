"""
submitter/collector.py

Recoge los resultados para entregarlos A MANO. La entrega del CTF es por
Outlook con inicio de sesión de la organización, que no se puede
automatizar: este servicio no envía nada a ningún sitio.

Vigila runtime/ en busca de factores encontrados y, si son válidos
(producto exacto, todos primos, número de la lista de objetivos actual y
no registrado antes), los anota en:

- runtime/para_entregar.txt: una línea legible por resultado, para copiar.
- runtime/solved_history.jsonl: el registro completo; main.py lo usa
  para no volver a atacar lo ya resuelto.
"""

import logging
import glob
import os
import shutil
import json
import threading
import time
import math
from watchdog.observers import Observer
from watchdog.events import FileSystemEventHandler

from targets import load_targets
from workers.quick_factor import is_probable_prime

log = logging.getLogger(__name__)

HISTORY_PATH = "runtime/solved_history.jsonl"
DELIVERY_PATH = "runtime/para_entregar.txt"
IGNORED_DIR = "runtime/ignored"
PARTIAL_DIR = "runtime/partial"

os.makedirs("runtime", exist_ok=True)

def verify_factors(n, factors):
    """True si `factors` es una factorización no trivial y exacta de n.
    Rechaza [n, 1]: ECM a veces "encuentra" N entero y el producto cuadra."""
    try:
        target_n = int(n)
        ints = [int(f) for f in factors]
    except (ValueError, TypeError):
        return False
    if len(ints) < 2 or any(f <= 1 for f in ints):
        return False
    return math.prod(ints) == target_n

def _as_int(n):
    try:
        return int(n)
    except (ValueError, TypeError):
        return None

def delivery_line(payload):
    """Línea para copiar en el correo de entrega."""
    n = payload["n"]
    factors = " * ".join(str(f) for f in sorted(int(f) for f in payload["factors"]))
    return (f"{time.strftime('%Y-%m-%d %H:%M')}  N ({len(n)} dígitos) = {n}\n"
            f"    factores: {factors}    [{payload.get('method', '?')}]\n")

def record(payload):
    with open(DELIVERY_PATH, "a") as f:
        f.write(delivery_line(payload))
    with open(HISTORY_PATH, "a") as f:
        json.dump(payload, f)
        f.write("\n")

def load_recorded(path=HISTORY_PATH):
    recorded = set()
    if os.path.exists(path):
        with open(path) as f:
            for line in f:
                try:
                    recorded.add(str(int(json.loads(line)["n"])))
                except (ValueError, KeyError, TypeError):
                    continue
    return recorded

class FactorCollector(FileSystemEventHandler):
    def __init__(self):
        super().__init__()
        # El barrido inicial (hilo principal) y el observer pueden ver el
        # mismo archivo a la vez; y un mismo N puede resolverlo ECM y GNFS
        # casi simultáneamente. Se registra una sola vez por N.
        self._lock = threading.Lock()
        self._recorded = load_recorded()

    def process(self, path):
        filename = os.path.basename(path)
        if not (filename.startswith("factor_found_") and filename.endswith(".json")):
            return

        with self._lock:
            try:
                with open(path, "r") as f:
                    payload = json.load(f)
            except FileNotFoundError:
                return  # ya procesado por otro evento
            except json.JSONDecodeError:
                # Escritor no atómico a medio escribir: llegará otro
                # on_modified cuando termine.
                return

            n = payload.get("n")
            factors = payload.get("factors", [])

            # Se relee en cada archivo: en un CTF se añaden retos sobre la
            # marcha, y el archivo es pequeño.
            targets = load_targets()
            if not verify_factors(n, factors):
                log.error(f"\n❌ ERROR DE VALIDACIÓN: {factors} no es una factorización no trivial de {n}.")
            elif targets is not None and _as_int(n) not in targets:
                # Restos de corridas o pruebas anteriores (o del benchmark):
                # no se entregan, pero se apartan en vez de borrarlos.
                log.info(f"[*] {n} no está en la lista de objetivos actual; "
                      f"no se registra (apartado en {IGNORED_DIR}/).")
                os.makedirs(IGNORED_DIR, exist_ok=True)
                shutil.move(path, os.path.join(IGNORED_DIR, filename))
                return
            elif not all(is_probable_prime(int(f)) for f in factors):
                # Se comprueba aquí en vez de fiarse de payload["complete"].
                # main.py escribirá la factorización completa cuando
                # resuelva los compuestos como sub-objetivos.
                log.info(f"[*] {n}: factorización parcial (hay factores compuestos); "
                      f"no se registra todavía (apartada en {PARTIAL_DIR}/).")
                os.makedirs(PARTIAL_DIR, exist_ok=True)
                shutil.move(path, os.path.join(PARTIAL_DIR, filename))
                return
            elif str(n) in self._recorded:
                log.info(f"[*] {n} ya registrado; se descarta duplicado de {payload.get('method')}.")
            else:
                record(payload)
                self._recorded.add(str(n))
                log.warning(f"[ENTREGAR A MANO] {delivery_line(payload).rstrip()}\n"
                            f"    (anotado en {DELIVERY_PATH})")

            try:
                os.remove(path)
            except FileNotFoundError:
                pass

    def process_existing(self, runtime_dir="runtime"):
        """Archivos escritos antes de arrancar (p.ej. por la Fase 0, que
        corre antes de levantar este servicio): el observer no los ve."""
        for path in sorted(glob.glob(os.path.join(runtime_dir, "factor_found_*.json"))):
            self.process(path)

    def on_created(self, event):
        if not event.is_directory:
            self.process(event.src_path)

    def on_modified(self, event):
        if not event.is_directory:
            self.process(event.src_path)

    def on_moved(self, event):
        # Los workers escriben .tmp y hacen os.replace(): eso llega como
        # "moved" con el nombre final en dest_path, no como "created".
        if not event.is_directory:
            self.process(event.dest_path)

if __name__ == "__main__":
    import log_setup
    log_setup.install_service()
    handler = FactorCollector()
    observer = Observer()
    observer.schedule(handler, path="runtime", recursive=False)
    observer.start()

    log.info(f"[*] Recolector de resultados iniciado (entrega manual: {DELIVERY_PATH}).")
    handler.process_existing()
    log.info(f"[*] Esperando archivos factor_found_*.json en 'runtime'...")

    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        log.info("\nDeteniendo monitorización...")
        observer.stop()
    observer.join()
