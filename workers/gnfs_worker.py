import threading
import re
import shlex
import json
import os
import shutil
import time
import socket
from workers.base_worker import BaseWorker


class GNFSWorker(BaseWorker):
    # Aunque resize() reanuda desde el snapshot de CADO (ver _restart_with),
    # cada reinicio sigue costando el arranque del servidor y las tareas en
    # vuelo -- cooldown más largo que el de BaseWorker.
    MIN_RESIZE_INTERVAL = 300.0

    def __init__(self):
        super().__init__("auto_gnfs", "CADO-NFS")
        # Ruta del parameter snapshot que CADO anuncia al arrancar ("can be
        # resumed with cado-nfs.py <snapshot>"). Su directorio es el workdir
        # con la base de datos de progreso (polinomio, relaciones...).
        self.snapshot = None

    def start(self, n: int, threads: int = 4, timeout: int = 86400, resume: str | None = None) -> None:
        if resume is None or n != self.current_n:
            resume = None
            self.snapshot = None
        source = shlex.quote(resume) if resume else str(int(n))
        self.current_n = n
        self.current_threads = threads

        cado_dir = os.path.abspath("bin/active/cado-nfs")
        hostname_file = f"{cado_dir}/.build_hostname"
        if os.path.exists(hostname_file):
            with open(hostname_file, "r") as f:
                build_hostname = f.read().strip()
            my_hostname = socket.gethostname()
            symlink_path = os.path.join(cado_dir, "build", my_hostname)
            if not os.path.lexists(symlink_path):
                try:
                    os.symlink(build_hostname, symlink_path)
                    print(f"[{self.method_name}] symlink creado: {symlink_path} -> {build_hostname}")
                except Exception as e:
                    print(f"[{self.method_name}] [!] No se pudo crear el symlink de build: {e}")

        # -u: sin buffer, para que _monitor vea las líneas según se
        # producen en vez de esperar a que se llene un buffer interno.
        # PYTHONPATH: cado-nfs-client.py necesita importar cadofactor de
        # scripts/, y como subproceso propio no lo hereda solo.
        # LD_LIBRARY_PATH: binarios como polyselect enlazan contra
        # librerías (p.ej. libhwloc) que solo existían en el contenedor
        # de build -- lib/ las trae bundladas (ver extract.sh).
        cmd = [
            "bash", "-c",
            f"cd {shlex.quote(cado_dir)} && "
            f"export PYTHONPATH={shlex.quote(cado_dir + '/scripts')}:$PYTHONPATH && "
            f"export LD_LIBRARY_PATH={shlex.quote(cado_dir + '/lib')}:$LD_LIBRARY_PATH && "
            f"exec python3 -u cado-nfs.py {source} -t {int(threads)}"
        ]
        generation = self._start_process(cmd, timeout=timeout)
        self.thread = threading.Thread(target=self._monitor, args=(n, generation))
        self.thread.daemon = True
        self.thread.start()

    def _restart_with(self, threads: int) -> None:
        # Reanudar desde el snapshot conserva el trabajo hecho (selección
        # polinómica, relaciones ya cribadas) en vez de empezar de cero.
        # CADO admite el snapshot más overrides en línea de comandos (-t).
        resume = self.snapshot if self.snapshot and os.path.exists(self.snapshot) else None
        if resume:
            print(f"[{self.method_name}] Reanudando desde {resume}")
        self.start(self.current_n, threads=threads, timeout=self.timeout, resume=resume)

    def discard_workdir(self) -> None:
        """Borra el workdir temporal de CADO del objetivo actual. Con kill()
        (SIGKILL) CADO no limpia su directorio en /tmp, y cada objetivo
        abandonado dejaba ahí sus relaciones. No usar entre resize(): el
        workdir es lo que permite reanudar."""
        if self.is_running():
            self.kill()
        if self.snapshot:
            workdir = os.path.dirname(self.snapshot)
            if os.path.basename(workdir).startswith("cado."):
                shutil.rmtree(workdir, ignore_errors=True)
            self.snapshot = None

    def _extract_verified_factors(self, text: str, n: int):
        """Busca cualquier combinación de números en `text` cuyo producto
        sea exactamente n. No depende de un formato de línea concreto --
        funciona aunque CADO-NFS los imprima juntos, separados, etc."""
        candidates = [int(x) for x in re.findall(r'\d{3,}', text)]
        candidates = [c for c in set(candidates) if 1 < c < n]
        for a in candidates:
            if n % a != 0:
                continue
            b = n // a
            if b in candidates or a * b == n:
                return [a, b]
        return None

    def _monitor(self, n: int, generation: int):
        accumulated = []
        for line in iter(self.process.stdout.readline, ''):
            clean_line = line.strip()
            if not clean_line:
                continue
            accumulated.append(clean_line)

            snap = re.search(r"can be resumed with \S+ (\S+parameters_snapshot\.\d+)", clean_line)
            if snap and generation == self._generation:
                self.snapshot = snap.group(1)

            progress_data = {
                "target_n": str(n),
                "status": "Running",
                "last_log": clean_line,
                "snapshot": self.snapshot,
                "timestamp": time.time(),
            }
            tmp_prog = "runtime/gnfs_progress.json.tmp"
            with open(tmp_prog, "w") as f:
                json.dump(progress_data, f, indent=2)
            os.replace(tmp_prog, "runtime/gnfs_progress.json")

            if "Total time" in clean_line:
                factors = self._extract_verified_factors("\n".join(accumulated), n)
                if factors:
                    self._write_success(n, factors, generation)
                else:
                    print(f"[{self.method_name}] [!] 'Total time' visto pero no se "
                          f"encontraron factores que verifiquen producto == n. "
                          f"Revisa el log completo.")
                break
        else:
            factors = self._extract_verified_factors("\n".join(accumulated), n)
            if factors:
                print(f"[{self.method_name}] Factores encontrados tras EOF (sin 'Total time'):")
                self._write_success(n, factors, generation)