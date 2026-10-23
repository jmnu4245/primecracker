#!/usr/bin/env python3
"""
main.py — Punto de entrada único. Cablea los módulos y conduce el bucle
objetivo por objetivo: barrido de vulnerabilidades primero (resuelve lo
fácil al instante), y de lo que queda, ataca del más pequeño al más
grande con YAFU o GNFS (según tamaño) + ECM, repartiendo el tiempo
disponible según el coste estimado de cada uno.
"""

import argparse
import json
import logging
import os
import sys
import subprocess
import threading
import time

import log_setup
from settings import SETTINGS
from targets import load_targets

log = logging.getLogger(__name__)

RUNTIME_DIR = "runtime"
CONFIG_DIR = "config"
SOLVED_HISTORY = os.path.join(RUNTIME_DIR, "solved_history.jsonl")
# Umbral YAFU/GNFS, hilos, límites y reparto del tiempo: config/settings.toml


def load_solved_history(path: str = SOLVED_HISTORY) -> set:
    """Números ya resueltos y verificados por el collector en corridas
    anteriores. Líneas corruptas (p.ej. un corte a mitad de escritura) se
    ignoran en vez de abortar la reanudación."""
    solved = set()
    if not os.path.exists(path):
        return solved
    with open(path) as f:
        for line in f:
            try:
                solved.add(int(json.loads(line)["n"]))
            except (ValueError, KeyError, TypeError):
                continue
    return solved


def pre_flight():
    log.info("[*] Ejecutando pre-flight checks (setup_toolchain)...")
    # Capturada y pasada al log: escrita directo al descriptor se saltaba
    # el archivo de log y salía desordenada respecto al resto de la salida.
    result = subprocess.run(["bash", "build/setup_toolchain.sh"],
                            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    log.info(result.stdout.rstrip())
    if result.returncode != 0:
        sys.exit("[!] Toolchain no verificada. Abortando antes del CTF.")
    os.makedirs(RUNTIME_DIR, exist_ok=True)


def phase_zero_cleanup(number_list_path: str) -> list:
    """Fase 0: barrido de vulnerabilidades de verdad. Resuelve al instante
    lo que tenga debilidad estructural (primos, potencias perfectas,
    primos cercanos, factor compartido entre módulos) y devuelve solo lo
    que de verdad necesita GNFS/ECM, ordenado de menor a mayor."""
    log.info("[*] Iniciando limpieza Fase 0 (barrido de vulnerabilidades)...")

    numbers = load_targets(number_list_path)
    if numbers is None:
        log.warning(f"[!] {number_list_path} no existe. Usando número RSA-100 de prueba.")
        numbers = [1522605027922533360535618378132637429718068114961380688657908494580122963258952897654000350692006139]

    # Reanudación: lo que el collector ya verificó y registró en una corrida
    # anterior no se vuelve a atacar.
    already = load_solved_history()
    if already:
        skipped = [n for n in numbers if n in already]
        numbers = [n for n in numbers if n not in already]
        log.info(f"[*] {len(skipped)} objetivo(s) ya resueltos en corridas anteriores, se saltan.")

    from screening.vulnerability_sweep import sweep
    solved, remaining = sweep(numbers)

    if solved:
        log.info(f"[+] Barrido resolvió {len(solved)}/{len(numbers)} al instante:")
        for n, factors in solved.items():
            log.info(f"    {n} = {' * '.join(factors)}")
    log.info(f"[*] Quedan {len(remaining)} para GNFS/ECM.")

    # De menor a mayor: así el pipeline ataca primero lo más barato de lo
    # que queda, aunque el sweep no lo haya podido resolver del todo.
    remaining.sort()
    return remaining


BACKGROUND_SERVICES = [
    "orchestrator/resource_monitor.py",
    "orchestrator/scoreboard_watcher.py",
    "submitter/collector.py",
]


def _forward_output(name, proc):
    """Reenvía la salida del servicio al log de main.py con su nivel
    original (el servicio escribe "NIVEL|mensaje", ver log_setup)."""
    service_log = logging.getLogger(f"service.{name}")
    for line in iter(proc.stdout.readline, ""):
        level, message = log_setup.parse_service_line(line.rstrip("\n"))
        service_log.log(level, f"[{name}] {message}")


def _launch_service(script):
    module = os.path.splitext(script)[0].replace(os.sep, ".")
    name = module.rsplit(".", 1)[-1]
    # -u: sin buffer, para que la salida llegue según se produce.
    # -m desde la raíz: así los servicios pueden importar módulos del
    # proyecto (p.ej. collector usa targets.py).
    env = dict(os.environ, **{log_setup.SERVICE_ENV: "1"})
    proc = subprocess.Popen([sys.executable, "-u", "-m", module], stdout=subprocess.PIPE,
                            stderr=subprocess.STDOUT, text=True, bufsize=1, env=env)
    threading.Thread(target=_forward_output, args=(name, proc), daemon=True).start()
    return proc


def start_background_services():
    processes = {}
    log.info("[*] Levantando servicios en background...")
    for script in BACKGROUND_SERVICES:
        if os.path.exists(script):
            log.info(f"    -> Iniciando {script}")
            processes[script] = _launch_service(script)
        else:
            log.warning(f"    [!] Aviso: no se encontró {script}. Saltando...")
    return processes


def restart_dead_services(processes):
    """Un servicio caído (p.ej. el submitter) dejaba de entregar resultados
    sin que nadie se enterase durante el resto de la corrida."""
    for script, proc in processes.items():
        if proc.poll() is not None:
            log.warning(f"[!] {script} terminó (código {proc.returncode}); relanzando.")
            processes[script] = _launch_service(script)


def anchor_method(target):
    """Método del ancla según el tamaño (nombre de la columna method del CSV)."""
    return "YAFU" if len(str(target)) < SETTINGS.gnfs_min_digits else "CADO-NFS"


def make_anchor_worker(target):
    """Worker principal ("ancla") según el tamaño; ECM corre siempre al lado."""
    if anchor_method(target) == "YAFU":
        from workers.yafu_worker import YAFUWorker
        return YAFUWorker()
    from workers.gnfs_worker import GNFSWorker
    return GNFSWorker()


def plan_budget(target, remaining_seconds, predictor, anchor_threads):
    """(segundos para atacar `target` ahora, estimación) -- con None como
    presupuesto si la estimación no cabe en lo que queda: mejor posponerlo
    que gastar el tiempo en algo que no va a terminar.

    La estimación sale del benchmark (medido con todos los núcleos) y se
    escala a los hilos que tendrá el ancla, suponiendo escalado lineal."""
    try:
        estimate, _ = predictor(len(str(target)), method=anchor_method(target))
        estimate *= (os.cpu_count() or anchor_threads) / anchor_threads
    except Exception:          # sin datos suficientes para ese método
        return min(SETTINGS.default_target_seconds, remaining_seconds), None
    budget = max(estimate * SETTINGS.budget_margin, SETTINGS.min_target_seconds)
    return (budget if budget <= remaining_seconds else None), estimate


def target_config(base_cfg, budget_seconds):
    """Config del controlador ajustada al plazo de este objetivo."""
    from dataclasses import replace
    minutes = budget_seconds / 60
    return replace(base_cfg, deadline_minutes=minutes,
                   variance_abort_minute=minutes * 0.3,
                   finish_margin_minutes=min(4.0, minutes * 0.1))


def release(worker, keep_workdir=False):
    """Para el worker y libera lo que deje en disco (el workdir de CADO: con
    SIGKILL CADO no limpia, y cada objetivo dejaba sus relaciones). Con
    keep_workdir se conserva para reanudar en la segunda pasada."""
    discard = getattr(worker, "discard_workdir", None)
    if discard and not keep_workdir:
        discard()
    else:
        worker.kill()


def attack_target(target, cfg, anchor_worker, ecm_worker, loop, max_seconds):
    """Ataca un único objetivo hasta resolverlo o agotar max_seconds.
    Devuelve los factores encontrados (quizá con compuestos) o None. No
    libera el ancla: lo decide quien llama."""
    log.info(f"\n{'='*60}\n[*] OBJETIVO: {target} ({len(str(target))} dígitos) "
          f"-- {anchor_worker.method_name} + ECM, {max_seconds / 60:.1f} min\n{'='*60}")

    ecm_worker.start(target, threads=cfg.ecm_min_threads)
    snapshot = getattr(anchor_worker, "snapshot", None)
    if snapshot and os.path.exists(snapshot):
        log.info(f"[*] Reanudando {anchor_worker.method_name} desde la pasada anterior.")
        anchor_worker.start(target, threads=cfg.gnfs_min_threads, resume=snapshot)
    else:
        anchor_worker.start(target, threads=cfg.gnfs_min_threads)

    deadline = time.time() + max_seconds
    result = None
    while time.time() < deadline:
        loop.tick()
        # Resultado en memoria, no glob sobre runtime/: el collector borra
        # los factor_found_*.json al procesarlos y el glob podía no verlos.
        for worker in (ecm_worker, anchor_worker):
            if worker.found.is_set():
                log.info(f"[+] Resuelto por {worker.method_name}: {worker.result}")
                result = worker.result
                break
        if result:
            break
        if not anchor_worker.is_running() and not ecm_worker.is_running():
            log.warning("[!] Ambos workers terminaron sin factor -- pasando al siguiente.")
            break
        time.sleep(loop.tick_seconds)

    ecm_worker.kill()
    return result


def run_control_loop(remaining_numbers, total_seconds, services=None):
    """Dos pasadas, de menor a mayor. En la primera cada objetivo recibe un
    presupuesto según su coste estimado y se pospone si no cabe; en la
    segunda, lo pospuesto y lo no resuelto se reintenta con todo el tiempo
    que quede (GNFS reanudando su criba, no desde cero)."""
    from orchestrator.controller import ControlLoop, Config
    from workers.ecm_worker import ECMWorker
    from workers.quick_factor import split_to_primes, write_result
    from benchmark.predict_cost import predict

    if not remaining_numbers:
        log.info("[+] No hay números restantes para factorizar. ¡Victoria!")
        return

    total, anchor_threads, ecm_min = SETTINGS.threads()
    base_cfg = Config(total_threads=total, ecm_min_threads=ecm_min, gnfs_min_threads=anchor_threads,
                      d_safe_bits=SETTINGS.d_safe_bits, ram_critical_pct=SETTINGS.ram_critical_pct,
                      temp_critical_c=SETTINGS.temp_critical_c,
                      cpu_headroom_pct=SETTINGS.cpu_headroom_pct)
    log.info(f"[*] Hilos: {total} en total, {anchor_threads} para el ancla, ECM desde {ecm_min}.")
    deadline_at = time.time() + total_seconds
    anchors = {}         # objetivo -> ancla, para reanudar en la 2ª pasada
    solved = set()
    log.info(f"[*] {len(remaining_numbers)} objetivos, {total_seconds / 60:.0f} min disponibles.")

    partial = {}         # objetivo -> factores ya encontrados (con compuestos)

    def attack(target, budget):
        """Factores encontrados para target (quizá con compuestos) o None."""
        if services:
            restart_dead_services(services)
        if target not in anchors:
            anchors[target] = make_anchor_worker(target)
        anchor = anchors[target]
        ecm_worker = ECMWorker()
        cfg = target_config(base_cfg, budget)
        # El controlador llama "gnfs" al ancla: sus reglas (kill por
        # recursos, kill-switch, varianza) aplican igual a YAFU.
        loop = ControlLoop(cfg, gnfs=anchor, ecm=ecm_worker, tick_seconds=5.0)
        try:
            factors = attack_target(target, cfg, anchor, ecm_worker, loop, budget)
        finally:
            ecm_worker.kill()
            release(anchor, keep_workdir=True)
        if factors:
            release(anchors.pop(target))
        return factors

    def solve(target, budget):
        """Factorización completa en primos de target, o None si no se
        consigue en `budget` segundos. Los factores compuestos que deje un
        worker (GNFS da dos factores; ECM, factor y cofactor) se atacan como
        sub-objetivos con el tiempo que quede."""
        deadline = time.time() + budget
        factors = partial.get(target) or attack(target, budget)
        if not factors:
            return None
        primes, composites = split_to_primes(factors, seconds=0)
        for composite in composites:
            remaining = deadline - time.time()
            log.info(f"[*] {target}: queda el factor compuesto {composite} "
                  f"({len(str(composite))} dígitos); se ataca como sub-objetivo.")
            sub = solve(composite, remaining) if remaining >= SETTINGS.min_target_seconds else None
            if sub is None:
                # Lo encontrado no se pierde: la segunda pasada parte de aquí.
                partial[target] = primes + [c for c in composites]
                return None
            primes += sub
        primes.sort()
        if composites:
            # Lo que escribió el worker era parcial (el collector lo apartó):
            # se escribe la factorización completa para que se registre.
            write_result(target, primes, "Combinado (sub-objetivos)", time.time() - deadline + budget)
        return primes

    try:
        retry = []
        log.info("\n[*] Primera pasada")
        for target in sorted(remaining_numbers):
            remaining = deadline_at - time.time()
            budget, estimate = plan_budget(target, remaining, predict, base_cfg.gnfs_min_threads)
            if budget is None:
                log.info(f"[*] {target} ({len(str(target))} dígitos): estimado "
                      f"{estimate / 60:.0f} min, no cabe en {remaining / 60:.0f} min -- pospuesto.")
                retry.append(target)
                continue
            if remaining < SETTINGS.min_target_seconds or solve(target, budget) is None:
                retry.append(target)
            else:
                solved.add(target)

        log.info("\n[*] Segunda pasada")
        for target in sorted(retry):
            remaining = deadline_at - time.time()
            if remaining < SETTINGS.min_target_seconds:
                log.info("[*] Sin tiempo para más objetivos.")
                break
            if solve(target, remaining) is not None:
                solved.add(target)
    except KeyboardInterrupt:
        log.info("\n[*] Interrupción detectada. Abortando el resto de objetivos.")
    finally:
        for anchor in anchors.values():
            release(anchor)

    log.info(f"\n[+] Resueltos {len(solved)}/{len(remaining_numbers)} objetivos restantes.")


def resolve_total_seconds(minutes=None, state_path="state.json"):
    """Tiempo total disponible: --minutes, si no time_remaining_seconds de
    state.json (el marcador), si no total_minutes de config/settings.toml.

    time_remaining_seconds es lo que quedaba cuando se escribió state.json,
    no ahora: se descuenta la antigüedad del archivo."""
    if minutes is not None:
        return minutes * 60
    try:
        with open(state_path) as f:
            remaining = int(json.load(f)["time_remaining_seconds"])
        age = time.time() - os.path.getmtime(state_path)
        return max(remaining - age, 0)
    except (OSError, ValueError, KeyError, TypeError):
        return SETTINGS.total_minutes * 60


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--minutes", type=float, default=None,
                        help="tiempo disponible (por defecto, time_remaining_seconds "
                             f"de state.json, o {SETTINGS.total_minutes:g} min)")
    parser.add_argument("--log-level", default="INFO",
                        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
                        help="nivel mínimo en consola (el archivo de log lo guarda todo)")
    args = parser.parse_args()

    log_setup.install(console_level=args.log_level)
    pre_flight()
    os.makedirs("data", exist_ok=True)
    target_file = "data/target_numbers.txt"
    total_seconds = resolve_total_seconds(args.minutes)

    remaining = phase_zero_cleanup(target_file)
    bg_processes = start_background_services()

    try:
        run_control_loop(remaining, total_seconds, services=bg_processes)
    finally:
        log.info("[*] Matando servicios en background...")
        for p in bg_processes.values():
            p.terminate()
        for p in bg_processes.values():
            try:
                p.wait(timeout=5)
            except subprocess.TimeoutExpired:
                p.kill()
        log.info("[+] Shutdown completo. Adiós.")


if __name__ == "__main__":
    main()
