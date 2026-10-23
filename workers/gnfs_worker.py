import logging
import threading
import re
from collections import deque
import shlex
import datetime
import json
import os
import shutil
import time
import socket

import psutil
from settings import SETTINGS
from workers.base_worker import BaseWorker

log = logging.getLogger(__name__)

_ANSI = re.compile(r"\x1b\[[0-9;]*m")
_ETA_LINE = re.compile(r"(?:Info:)?([^:]+): Marking workunit \S+ as \S+ "
                       r"\((\d+(?:\.\d+)?)% => ETA (.+)\)")

# Tras la criba quedan filtrado, álgebra lineal y raíz cuadrada. Su coste
# relativo a la criba, estimado por encima: con c60-c100 suele estar en el
# 15-30% del tiempo de criba. Pesimista a propósito: el controlador lo usa
# para abortar, y es mejor abortar un poco antes que perder el objetivo.
POST_SIEVE_FACTOR = 0.35


def parse_eta_line(line):
    """(fase, porcentaje, datetime de ETA) de una línea de progreso de CADO,
    o None. CADO colorea con ANSI aunque la salida vaya a un pipe."""
    m = _ETA_LINE.search(_ANSI.sub("", line))
    if not m:
        return None
    try:
        eta = datetime.datetime.strptime(" ".join(m.group(3).split()), "%a %b %d %H:%M:%S %Y")
    except ValueError:
        return None
    return m.group(1).strip(), float(m.group(2)), eta


def project_finish_at(eta, sieve_started_at):
    """Instante (epoch) en que se espera que GNFS termine entero, a partir
    de la ETA de la criba. Absoluto y no relativo al arranque del worker:
    resize() relanza el proceso y ese arranque se movía."""
    sieve_seconds = max(eta - sieve_started_at, 0.0)
    return eta + POST_SIEVE_FACTOR * sieve_seconds


class GNFSWorker(BaseWorker):
    # Aunque resize() reanuda desde el snapshot de CADO (ver _restart_with),
    # cada reinicio sigue costando el arranque del servidor y las tareas en
    # vuelo -- cooldown más largo que el de BaseWorker.
    MIN_RESIZE_INTERVAL = 300.0
    # En disco y no en /tmp, que en Fedora es tmpfs (RAM): las relaciones de
    # un c100+ ocupan GB y competían con la propia factorización por memoria.
    WORKDIR_ROOT = os.path.join("runtime", "cado")

    def __init__(self):
        super().__init__("auto_gnfs", "CADO-NFS")
        # Ruta del parameter snapshot que CADO anuncia al arrancar ("can be
        # resumed with cado-nfs.py <snapshot>"). Vive en self.workdir, con
        # la base de datos de progreso (polinomio, relaciones...).
        self.snapshot = None
        self.workdir = None

    def start(self, n: int, threads: int = 4, timeout: int = 86400, resume: str | None = None) -> None:
        if resume is None or n != self.current_n:
            resume = None
            self.snapshot = None
        if resume:
            # El snapshot ya lleva tasks.workdir; CADO rechaza dos workdir.
            source = shlex.quote(resume)
        else:
            self.workdir = os.path.abspath(os.path.join(
                self.WORKDIR_ROOT, f"c{len(str(int(n)))}_{int(time.time() * 1000)}"))
            os.makedirs(self.workdir)
            source = f"{int(n)} --workdir {shlex.quote(self.workdir)}"
        self.current_n = n
        self.current_threads = threads

        cado_dir = os.path.abspath(SETTINGS.binary("cado-nfs"))
        hostname_file = f"{cado_dir}/.build_hostname"
        if os.path.exists(hostname_file):
            with open(hostname_file, "r") as f:
                build_hostname = f.read().strip()
            my_hostname = socket.gethostname()
            symlink_path = os.path.join(cado_dir, "build", my_hostname)
            if not os.path.lexists(symlink_path):
                try:
                    os.symlink(build_hostname, symlink_path)
                    log.info(f"[{self.method_name}] symlink creado: {symlink_path} -> {build_hostname}")
                except Exception as e:
                    log.warning(f"[{self.method_name}] [!] No se pudo crear el symlink de build: {e}")

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
            log.info(f"[{self.method_name}] Reanudando desde {resume}")
        self.start(self.current_n, threads=threads, timeout=self.timeout, resume=resume)

    def discard_workdir(self) -> None:
        """Borra el workdir de CADO del objetivo actual. Con kill() (SIGKILL)
        CADO no limpia, y cada objetivo abandonado dejaba sus relaciones. No
        usar entre resize() ni si se va a volver al objetivo: el workdir es
        lo que permite reanudar."""
        if self.is_running():
            self.kill()
        if self.workdir:
            self._kill_daemon_clients(self.workdir)
            shutil.rmtree(self.workdir, ignore_errors=True)
        self.workdir = None
        self.snapshot = None

    def kill(self):
        super().kill()
        # Los cado-nfs-client se lanzan con --daemon: salen del grupo de
        # proceso y el killpg de BaseWorker no los alcanza. Sin esto, tras
        # resize() o al pasar al siguiente objetivo seguían cribando (`las`)
        # en segundo plano, y recreaban workdir/client tras un rmtree.
        if self.workdir:
            self._kill_daemon_clients(self.workdir)

    @staticmethod
    def _kill_daemon_clients(workdir: str) -> None:
        victims = []
        for proc in psutil.process_iter(["cmdline"]):
            cmdline = " ".join(proc.info["cmdline"] or [])
            # Solo clientes de CADO de este workdir (y sus hijos): no
            # cualquier proceso que mencione la ruta, como un `tail -f`.
            if "cado-nfs-client.py" in cmdline and workdir in cmdline:
                try:
                    victims += [proc] + proc.children(recursive=True)
                except psutil.NoSuchProcess:
                    pass
        for proc in victims:
            try:
                proc.kill()
            except psutil.NoSuchProcess:
                pass
        psutil.wait_procs(victims, timeout=5)

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
        # CADO imprime los factores en la última línea, *después* de "Total
        # time": antes se dejaba de leer en "Total time" y nunca se veían
        # (por eso el benchmark no tenía ni un éxito de CADO-NFS). Se lee
        # hasta EOF y se buscan en las últimas líneas.
        tail = deque(maxlen=20)
        proc = self.process
        sieve_started_at = None
        projected_finish_at = None
        phase = None
        for line in iter(proc.stdout.readline, ''):
            clean_line = line.strip()
            if not clean_line:
                continue
            tail.append(clean_line)

            snap = re.search(r"can be resumed with \S+ (\S+parameters_snapshot\.\d+)", clean_line)
            if snap and generation == self._generation:
                self.snapshot = snap.group(1)

            parsed = parse_eta_line(clean_line)
            if parsed:
                phase, _, eta = parsed
                # Solo la ETA de la criba permite proyectar el total; la de
                # la selección polinómica es solo de esa fase.
                if phase == "Lattice Sieving":
                    if sieve_started_at is None:
                        sieve_started_at = time.time()
                    projected_finish_at = project_finish_at(eta.timestamp(), sieve_started_at)

            progress_data = {
                "target_n": str(n),
                "status": "Running",
                "phase": phase,
                "projected_finish_at": projected_finish_at,
                "last_log": clean_line,
                "snapshot": self.snapshot,
                "timestamp": time.time(),
            }
            tmp_prog = "runtime/gnfs_progress.json.tmp"
            with open(tmp_prog, "w") as f:
                json.dump(progress_data, f, indent=2)
            os.replace(tmp_prog, "runtime/gnfs_progress.json")

        returncode = proc.wait()
        if generation != self._generation:
            return  # proceso reemplazado por resize(): nada que reportar
        factors = self._extract_verified_factors("\n".join(tail), n)
        if factors:
            self._write_success(n, factors, generation)
        elif returncode == 0:
            # ("Total time" no sirve como señal de fin: CADO lo imprime al
            # terminar cada fase.)
            log.warning(f"[{self.method_name}] [!] CADO terminó sin error pero no se "
                  f"encontraron factores que verifiquen producto == n.")
