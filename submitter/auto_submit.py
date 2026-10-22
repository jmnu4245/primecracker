"""
submitter/auto_submit.py

Entrega de resultados de un reto CTF de factorización. Vigila runtime/ en
busca de factores encontrados, verifica que su producto reconstruye el
número de reto y los registra.

NOTA: el "envío" es una SIMULACIÓN local (send_email_simulated). No abre
ninguna conexión ni envía datos a ningún servicio externo; solo imprime
y escribe en runtime/solved_history.jsonl. EMAIL_ACCOUNT se lee de .env
solo para rellenar el payload simulado con tu propia dirección.
"""

import glob
import os
import json
import threading
import time
import math
from watchdog.observers import Observer
from watchdog.events import FileSystemEventHandler
from dotenv import load_dotenv

# Cargar configuración desde .env
load_dotenv()
EMAIL_ACCOUNT = os.getenv("EMAIL_ACCOUNT", "email_no_configurado@ejemplo.com")
HISTORY_PATH = "runtime/solved_history.jsonl"

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

def send_email_simulated(payload):
    print("\n" + "="*50)
    print("🟢 SIMULACIÓN DE ENVÍO ACTIVA")
    print("="*50)
    print("📡 Haciendo POST / Enviando correo...")
    print(f"✉️  De:   {EMAIL_ACCOUNT}")
    print(f"🎯 Para: {EMAIL_ACCOUNT}")
    print("📦 Payload adjunto:")
    print(json.dumps(payload, indent=2))
    print("="*50)
    print("✅ Resultado: 200 OK - Factor enviado y registrado correctamente.\n")
    return True

def load_submitted(path=HISTORY_PATH):
    submitted = set()
    if os.path.exists(path):
        with open(path) as f:
            for line in f:
                try:
                    submitted.add(str(int(json.loads(line)["n"])))
                except (ValueError, KeyError, TypeError):
                    continue
    return submitted

class FactorSubmitter(FileSystemEventHandler):
    def __init__(self):
        super().__init__()
        # El barrido inicial (hilo principal) y el observer pueden ver el
        # mismo archivo a la vez; y un mismo N puede resolverlo ECM y GNFS
        # casi simultáneamente. Se entrega una sola vez por N.
        self._lock = threading.Lock()
        self._submitted = load_submitted()

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

            if not verify_factors(n, factors):
                print(f"\n❌ ERROR DE VALIDACIÓN: {factors} no es una factorización no trivial de {n}.")
            elif str(n) in self._submitted:
                print(f"[*] {n} ya entregado; se descarta duplicado de {payload.get('method')}.")
            else:
                send_email_simulated(payload)
                with open(HISTORY_PATH, "a") as log_file:
                    json.dump(payload, log_file)
                    log_file.write("\n")
                self._submitted.add(str(n))

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
    handler = FactorSubmitter()
    observer = Observer()
    observer.schedule(handler, path="runtime", recursive=False)
    observer.start()

    print(f"[*] Iniciando Submitter...")
    print(f"[*] Correo cargado desde .env: {EMAIL_ACCOUNT}")
    handler.process_existing()
    print(f"[*] Esperando archivos factor_found_*.json en 'runtime'...")

    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        print("\nDeteniendo monitorización...")
        observer.stop()
    observer.join()
