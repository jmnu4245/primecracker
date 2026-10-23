"""
scripts/smoke_workers.py

Prueba manual de los tres workers con los binarios reales: arranque,
is_running(), resize() y kill() sobre RSA-100 (que no factorizan en los
pocos segundos que dura, a propósito). No lo recoge pytest.

Uso (desde la raíz):  python3 -m scripts.smoke_workers
"""

import logging
import os
import time

from workers.ecm_worker import ECMWorker
from workers.gnfs_worker import GNFSWorker
from workers.yafu_worker import YAFUWorker

RSA_100 = 1522605027922533360535618378132637429718068114961380688657908494580122963258952897654000350692006139


def smoke(worker_class, n):
    print(f"\n{'=' * 40}\n[*] {worker_class.__name__}\n{'=' * 40}")
    worker = worker_class()
    worker.MIN_RESIZE_INTERVAL = 0   # para poder probar resize() enseguida
    worker.start(n, threads=2)
    time.sleep(3)
    if not worker.is_running():
        print("[-] El proceso no está corriendo tras arrancar.")
        return False
    print("[+] is_running() tras start(): True")

    worker.resize(4)
    time.sleep(5)
    print(f"[{'+' if worker.is_running() else '-'}] is_running() tras resize(4): "
          f"{worker.is_running()} (hilos: {worker.current_threads})")

    worker.kill()
    ok = not worker.is_running()
    print(f"[{'+' if ok else '-'}] is_running() tras kill(): {worker.is_running()}")
    # Sin restos: workdir de YAFU/CADO, y un resultado si por casualidad lo hubo.
    if worker.result_path and os.path.exists(worker.result_path):
        os.remove(worker.result_path)
    discard = getattr(worker, "discard_workdir", None)
    if discard:
        discard()
    return ok


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    results = {cls.__name__: smoke(cls, RSA_100) for cls in (ECMWorker, YAFUWorker, GNFSWorker)}
    print("\n" + "\n".join(f"{name}: {'OK' if ok else 'FALLO'}" for name, ok in results.items()))
