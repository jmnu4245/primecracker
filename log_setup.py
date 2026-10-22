"""
log_setup.py

Copia todo lo que imprime el proceso (incluidos los hilos de los workers)
a runtime/primecracker.log, con marca de tiempo, sin tocar los print() del
código. main.py lo instala al arrancar y reenvía también la salida de los
servicios en background, así una corrida larga queda registrada entera en
un solo archivo.
"""

import os
import sys
import threading
import time

LOG_PATH = os.path.join("runtime", "primecracker.log")


class _Tee:
    def __init__(self, stream, log_file, lock):
        self._stream = stream
        self._log = log_file
        self._lock = lock
        self._pending = ""

    def write(self, data):
        self._stream.write(data)
        with self._lock:
            # Solo líneas completas: los workers imprimen desde varios
            # hilos y una línea partida en dos write() no debe llevar dos
            # marcas de tiempo.
            self._pending += data
            *lines, self._pending = self._pending.split("\n")
            stamp = time.strftime("%Y-%m-%d %H:%M:%S")
            for line in lines:
                self._log.write(f"{stamp} {line}\n")
        return len(data)

    def flush(self):
        self._stream.flush()
        self._log.flush()

    def __getattr__(self, attr):
        # isatty(), fileno(), encoding... del stream original
        return getattr(self._stream, attr)


def install(path=LOG_PATH):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    log_file = open(path, "a", buffering=1, encoding="utf-8")
    lock = threading.Lock()
    sys.stdout = _Tee(sys.stdout, log_file, lock)
    sys.stderr = _Tee(sys.stderr, log_file, lock)
    print(f"{'#' * 20} nueva corrida (pid {os.getpid()}) {'#' * 20}")
