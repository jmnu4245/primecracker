import math
import re
import shlex
import threading
from settings import SETTINGS
from workers.base_worker import BaseWorker


class ECMWorker(BaseWorker):
    # GMP-ECM es monohilo: "threads" se materializa como N procesos ecm en
    # paralelo dentro del mismo grupo de proceso (kill() los mata a todos).
    # Antes `threads` se ignoraba y cada resize() relanzaba el mismo proceso
    # único, perdiendo progreso sin ganar paralelismo.
    BINARY = SETTINGS.binary("ecm_bin")
    TOTAL_CURVES = 50

    def __init__(self):
        super().__init__("auto_ecm", "GMP-ECM")
        self.b1 = 100000

    def start(self, n: int, threads: int = 1, b1: int = 100000, timeout: int = 3600) -> None:
        n = int(n)
        threads = max(1, int(threads))
        self.current_n = n
        self.current_threads = threads
        self.b1 = b1

        # Las curvas se reparten: más procesos = el mismo esfuerzo total
        # terminado antes, no más esfuerzo.
        curves = max(1, math.ceil(self.TOTAL_CURVES / threads))
        # -one: parar en el primer factor aunque el cofactor sea compuesto.
        # stdbuf -oL: con stdout a un pipe, ecm bufferiza por bloques y la
        # línea "Factor found" solo llegaba al terminar el proceso.
        one_ecm = f"echo {n} | stdbuf -oL {shlex.quote(self.BINARY)} -one -c {curves} {int(b1)}"
        cmd = ["bash", "-c", f"for i in $(seq {threads}); do {one_ecm} & done; wait"]
        generation = self._start_process(cmd, timeout=timeout)
        self.thread = threading.Thread(target=self._monitor, args=(n, generation))
        self.thread.daemon = True
        self.thread.start()

    def _restart_with(self, threads: int) -> None:
        self.start(self.current_n, threads=threads, b1=self.b1, timeout=self.timeout)

    def _monitor(self, n: int, generation: int):
        for line in iter(self.process.stdout.readline, ''):
            match = re.search(r'Factor found in step \d+:\s*(\d+)', line)
            if not match:
                continue
            factor = int(match.group(1))
            # ECM a veces "encuentra" N entero (todas las curvas a la vez):
            # no es una factorización, se sigue esperando a otra curva.
            if not 1 < factor < n:
                continue
            if self._write_success(n, [factor, n // factor], generation):
                break
