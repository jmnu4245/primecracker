import threading
import json
import os
from workers.base_worker import BaseWorker


class YAFUWorker(BaseWorker):
    BINARY = "bin/active/yafu_bin"

    def __init__(self):
        super().__init__("auto_yafu", "YAFU")

    def start(self, n: int, threads: int = 4, timeout: int = 3600) -> None:
        self.current_n = n
        self.current_threads = threads

        # La expresión va por stdin: si stdin no es una terminal (servicio,
        # pipe, cron...) YAFU 3.x lo lee como batchfile e ignora el número
        # pasado como argumento, y terminaba sin factorizar nada.
        cmd = [self.BINARY, "-threads", str(int(threads))]
        if os.path.exists("factor.json"):
            os.remove("factor.json")
        generation = self._start_process(cmd, stdin_data=f"factor({int(n)})\n", timeout=timeout)
        self.thread = threading.Thread(target=self._monitor, args=(n, generation))
        self.thread.daemon = True
        self.thread.start()

    def _restart_with(self, threads: int) -> None:
        self.start(self.current_n, threads=threads, timeout=self.timeout)

    def _monitor(self, n: int, generation: int):
        self.process.wait()
        if os.path.exists("factor.json"):
            try:
                with open("factor.json", "r") as f:
                    data = json.load(f)
                if data.get("input-decimal") == str(n):
                    factors = [int(f) for f in data.get("factors-prime", [])]
                    if factors:
                        self._write_success(n, factors, generation)
            except Exception as e:
                print(f"[YAFU Worker] Error parseando factor.json: {e}")