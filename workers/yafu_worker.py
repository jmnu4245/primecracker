import logging
import threading
import json
import os
import shutil
import time
from settings import SETTINGS
from workers.base_worker import BaseWorker

log = logging.getLogger(__name__)


class YAFUWorker(BaseWorker):
    BINARY = SETTINGS.binary("yafu_bin")
    # YAFU escribe en su directorio actual (factor.json, siqs.dat,
    # session.log...). Antes era la raíz del proyecto: dos ejecuciones se
    # pisaban factor.json, y siqs.dat (24 MB) quedaba allí.
    WORKDIR_ROOT = os.path.join("runtime", "yafu")

    def __init__(self):
        super().__init__("auto_yafu", "YAFU")
        self.workdir = None

    def start(self, n: int, threads: int = 4, timeout: int = 3600) -> None:
        if self.workdir is None or n != self.current_n:
            self.discard_workdir()
            self.workdir = os.path.abspath(os.path.join(
                self.WORKDIR_ROOT, f"c{len(str(int(n)))}_{int(time.time() * 1000)}"))
            os.makedirs(self.workdir)
        self.current_n = n
        self.current_threads = threads

        # siqs.dat se borra siempre: YAFU reanudaría la criba desde él, pero
        # el que deja un proceso matado con SIGKILL (resize, abandono) queda
        # truncado y YAFU se colgaba al arrancar SIQS sin avanzar.
        for stale in ("factor.json", "siqs.dat"):
            path = os.path.join(self.workdir, stale)
            if os.path.exists(path):
                os.remove(path)
        # La expresión va por stdin: si stdin no es una terminal (servicio,
        # pipe, cron...) YAFU 3.x lo lee como batchfile e ignora el número
        # pasado como argumento, y terminaba sin factorizar nada.
        cmd = [os.path.abspath(self.BINARY), "-threads", str(int(threads))]
        generation = self._start_process(cmd, stdin_data=f"factor({int(n)})\n",
                                         timeout=timeout, cwd=self.workdir)
        self.thread = threading.Thread(target=self._monitor, args=(n, generation))
        self.thread.daemon = True
        self.thread.start()

    def _restart_with(self, threads: int) -> None:
        self.start(self.current_n, threads=threads, timeout=self.timeout)

    def discard_workdir(self) -> None:
        """Para YAFU y borra su directorio de trabajo del objetivo actual."""
        if self.is_running():
            self.kill()
        if self.workdir:
            shutil.rmtree(self.workdir, ignore_errors=True)
        self.workdir = None

    def _monitor(self, n: int, generation: int):
        proc, workdir = self.process, self.workdir
        assert proc is not None and workdir is not None
        # Hay que leer la salida aunque no se use: es un pipe, y con más de
        # 64 KB sin leer YAFU se bloqueaba escribiendo (anon_pipe_write) sin
        # avanzar hasta que el watchdog lo mataba por timeout.
        for _ in iter(proc.stdout.readline, ''):
            pass
        proc.wait()
        factor_json = os.path.join(workdir, "factor.json")
        if not os.path.exists(factor_json):
            return
        try:
            with open(factor_json, "r") as f:
                data = json.load(f)
        except (OSError, json.JSONDecodeError) as e:
            log.error(f"[YAFU Worker] Error leyendo {factor_json}: {e}")
            return
        if data.get("input-decimal") == str(n):
            factors = [int(f) for f in data.get("factors-prime", [])]
            if factors:
                self._write_success(n, factors, generation)
