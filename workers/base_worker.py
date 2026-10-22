import math
import subprocess
import threading
import json
import os
import signal
import time


class BaseWorker:
    # Segundos mínimos entre dos reinicios por resize(). Matar y relanzar
    # estas herramientas de línea de comandos es la única forma real de
    # "redimensionar" (ninguna soporta cambiar hilos en caliente), pero
    # cada reinicio tira todo el progreso interno a la basura. Sin este
    # freno, un AIMD que reevalúa cada pocos segundos puede matar el
    # proceso antes de que llegue a hacer ningún trabajo útil -- que es
    # exactamente lo que vimos pasar.
    MIN_RESIZE_INTERVAL = 30.0

    def __init__(self, target_id: str, method_name: str):
        self.process = None
        self.thread = None
        self.watchdog = None
        self.start_time = 0.0
        self.timeout = 0
        self.target_id = target_id
        self.method_name = method_name
        self.current_n = None
        self.current_threads = 1
        self._generation = 0
        self._last_resize_time = 0.0
        # Canal de resultado en memoria: quien orquesta consulta `found` en
        # vez de vigilar runtime/ por glob (auto_submit borra esos archivos
        # tras procesarlos, así que el glob podía no verlos nunca).
        self.result = None
        self.found = threading.Event()
        os.makedirs("runtime", exist_ok=True)

    def _start_process(self, cmd, stdin_data=None, timeout=3600):
        """Lanza el proceso y devuelve su generación. Los _monitor de cada
        subclase deben pasarla a _write_success para que un monitor de un
        proceso ya reemplazado por resize() no pueda dar éxito (ni matar
        al proceso nuevo)."""
        self.start_time = time.time()
        self.timeout = timeout
        self._generation += 1
        my_generation = self._generation
        self.result = None
        self.found.clear()

        self.process = subprocess.Popen(
            cmd,
            stdin=subprocess.PIPE if stdin_data else None,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
            start_new_session=True,  # grupo de proceso propio -- ver kill()
        )

        if stdin_data:
            self.process.stdin.write(stdin_data)
            self.process.stdin.flush()
            self.process.stdin.close()

        # Con generación: si llega un resize() y se relanza el proceso,
        # este watchdog se apaga solo en vez de quedar duplicado vigilando
        # (sin querer) al proceso nuevo con el reloj del viejo.
        self.watchdog = threading.Thread(target=self._watchdog_loop, args=(my_generation,))
        self.watchdog.daemon = True
        self.watchdog.start()
        return my_generation

    def _watchdog_loop(self, generation):
        while self._generation == generation and self.is_running():
            if time.time() - self.start_time > self.timeout:
                print(f"[!] {self.method_name} excedió el timeout de {self.timeout}s. Abortando.")
                self.kill()
                break
            time.sleep(1)

    def _write_success(self, n: int, factors: list, generation=None) -> bool:
        """Registra un resultado solo si es una factorización no trivial y
        exacta de n. Devuelve False (sin efectos) si no lo es o si viene de
        un proceso de una generación ya reemplazada."""
        if generation is not None and generation != self._generation:
            return False
        factors = [int(f) for f in factors]
        if len(factors) < 2 or any(f <= 1 for f in factors) or math.prod(factors) != n:
            print(f"[{self.method_name}] [!] Factorización descartada: "
                  f"{factors} no es una factorización no trivial de {n}.")
            return False

        payload = {
            "target_id": self.target_id,
            "n": str(n),
            "factors": [str(f) for f in factors],
            "method": self.method_name,
            "time_elapsed_seconds": round(time.time() - self.start_time, 4),
        }
        # Milisegundos: con segundos, dos éxitos del mismo worker en el
        # mismo segundo se pisaban el archivo.
        final_filename = f"runtime/factor_found_{self.target_id}_{int(time.time() * 1000)}.json"
        tmp_filename = final_filename + ".tmp"
        with open(tmp_filename, "w") as f:
            json.dump(payload, f, indent=2)
        os.replace(tmp_filename, final_filename)
        self.result = factors
        self.kill()
        self.found.set()  # después de kill(): quien vea found ya ve el proceso parado
        return True

    def resize(self, threads: int) -> None:
        """Cooldown + debounce compartido por los tres workers. La
        mecánica real de reinicio (qué parámetros propios pasar a start())
        la define cada subclase en _restart_with()."""
        if not self.is_running() or self.current_n is None:
            return
        if threads == self.current_threads:
            return

        elapsed = time.time() - self._last_resize_time
        if elapsed < self.MIN_RESIZE_INTERVAL:
            print(f"[{self.method_name}] resize({threads}) en cooldown "
                  f"({elapsed:.0f}s/{self.MIN_RESIZE_INTERVAL:.0f}s) -- ignorado.")
            return

        self._last_resize_time = time.time()
        # Si se pierde o no el progreso depende de _restart_with(): GNFS
        # reanuda desde su snapshot, ECM vuelve a lanzar curvas nuevas.
        print(f"[{self.method_name}] Reiniciando con {threads} hilos...")
        self.kill()
        self._restart_with(threads)

    def _restart_with(self, threads: int) -> None:
        raise NotImplementedError(f"{self.method_name} debe implementar _restart_with().")

    def kill(self):
        if not self.process or self.process.poll() is not None:
            return
        # Grupo de proceso completo, no solo el proceso de nivel superior.
        # GNFS en concreto lanza su propio árbol (cado-nfs-client.py, que
        # a su vez lanza polyselect/las/bwc); matar solo el padre deja
        # huérfanos consumiendo CPU en segundo plano -- justo el tipo de
        # ruido que puede estar contaminando las lecturas de cpu_percent
        # que ve el AIMD.
        try:
            os.killpg(os.getpgid(self.process.pid), signal.SIGKILL)
        except ProcessLookupError:
            pass
        except Exception:
            self.process.kill()  # fallback si no hay grupo de proceso por algún motivo
        # Recoger el proceso: sin esto is_running() podía seguir diciendo
        # True un instante después de kill(), y resize() relanzaba encima.
        try:
            self.process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            print(f"[{self.method_name}] [!] El proceso no terminó 5s después de SIGKILL.")

    def is_running(self) -> bool:
        return self.process is not None and self.process.poll() is None