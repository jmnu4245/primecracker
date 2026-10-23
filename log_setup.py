"""
log_setup.py

Configuración de logging del proyecto.

- install(): para main.py. Consola con solo el mensaje (el mismo aspecto
  que tenían los print()) filtrable por nivel, y runtime/primecracker.log
  con fecha, nivel y módulo de cada línea.
- install_service(): para los servicios que lanza main.py. Escriben
  "NIVEL|mensaje" por stdout; main.py lo lee con parse_service_line() y lo
  vuelve a emitir con su nivel, así acaba en el mismo log.
"""

import logging
import os
import sys
import threading

LOG_PATH = os.path.join("runtime", "primecracker.log")
FILE_FORMAT = "%(asctime)s %(levelname)-7s %(name)s: %(message)s"
# main.py lo pone en el entorno de los servicios que lanza.
SERVICE_ENV = "PRIMECRACKER_SERVICE"


def _log_uncaught():
    """Excepciones no capturadas (también en hilos, p.ej. el _monitor de un
    worker) al log en vez de solo a stderr, donde no quedaba rastro."""
    def hook(exc_type, exc, tb):
        logging.getLogger("uncaught").error("Excepción no controlada", exc_info=(exc_type, exc, tb))
    sys.excepthook = hook
    threading.excepthook = lambda args: hook(args.exc_type, args.exc_value, args.exc_traceback)


def install(path=LOG_PATH, console_level=logging.INFO):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    console = logging.StreamHandler(sys.stdout)
    console.setLevel(console_level)
    console.setFormatter(logging.Formatter("%(message)s"))
    logfile = logging.FileHandler(path, encoding="utf-8")
    logfile.setLevel(logging.DEBUG)
    logfile.setFormatter(logging.Formatter(FILE_FORMAT))

    root = logging.getLogger()
    root.setLevel(logging.DEBUG)
    root.handlers = [console, logfile]
    _log_uncaught()
    logging.getLogger(__name__).info(f"{'#' * 20} nueva corrida (pid {os.getpid()}) {'#' * 20}")


class _ServiceFormatter(logging.Formatter):
    def format(self, record):
        # Cada línea con su nivel: los mensajes de varias líneas (el payload
        # del submitter, una traza) no deben perderlo al partirse.
        text = super().format(record)
        return "\n".join(f"{record.levelname}|{line}" for line in text.splitlines() or [""])


def install_service():
    """Para el __main__ de cada servicio. Lanzado a mano (sin main.py) usa
    un formato normal en vez del "NIVEL|mensaje"."""
    handler = logging.StreamHandler(sys.stdout)
    if os.environ.get(SERVICE_ENV):
        handler.setFormatter(_ServiceFormatter("%(message)s"))
    else:
        handler.setFormatter(logging.Formatter("%(levelname)s %(message)s"))
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    root.handlers = [handler]
    _log_uncaught()


def parse_service_line(line):
    """(nivel, mensaje) de una línea de un servicio. Lo que no trae nivel
    (p.ej. una traza escrita directamente en stderr) cuenta como ERROR."""
    name, sep, message = line.partition("|")
    level = logging.getLevelName(name) if sep else None
    if isinstance(level, int):
        return level, message
    return logging.ERROR, line
