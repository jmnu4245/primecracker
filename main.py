#!/usr/bin/env python3
"""
main.py — Punto de entrada único. Cablea los módulos y conduce el bucle
objetivo por objetivo: barrido de vulnerabilidades primero (resuelve lo
fácil al instante), y de lo que queda, ataca del más pequeño al más
grande con GNFS+ECM.
"""

import json
import os
import sys
import subprocess
import threading
import time

import log_setup

RUNTIME_DIR = "runtime"
CONFIG_DIR = "config"
SOLVED_HISTORY = os.path.join(RUNTIME_DIR, "solved_history.jsonl")

# Por debajo de este tamaño el "ancla" es YAFU (SIQS), no CADO-NFS. Según
# benchmark_results.csv, YAFU resuelve 80 dígitos en ~150s mientras CADO
# agota 600s; y por debajo de ~60 dígitos CADO ni arranca. El cruce
# habitual SIQS/GNFS está en torno a 95-100 dígitos.
GNFS_MIN_DIGITS = 95


def load_solved_history(path: str = SOLVED_HISTORY) -> set:
    """Números ya resueltos y verificados por auto_submit en corridas
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
    print("[*] Ejecutando pre-flight checks (setup_toolchain)...")
    # Capturada y reimpresa: escrita directo al descriptor se saltaba el
    # log de log_setup y salía desordenada respecto a los print() de main.
    result = subprocess.run(["bash", "build/setup_toolchain.sh"],
                            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    print(result.stdout, end="")
    if result.returncode != 0:
        sys.exit("[!] Toolchain no verificada. Abortando antes del CTF.")
    os.makedirs(RUNTIME_DIR, exist_ok=True)


def phase_zero_cleanup(number_list_path: str) -> list:
    """Fase 0: barrido de vulnerabilidades de verdad. Resuelve al instante
    lo que tenga debilidad estructural (primos, potencias perfectas,
    primos cercanos, factor compartido entre módulos) y devuelve solo lo
    que de verdad necesita GNFS/ECM, ordenado de menor a mayor."""
    print("[*] Iniciando limpieza Fase 0 (barrido de vulnerabilidades)...")

    if not os.path.exists(number_list_path):
        print(f"[!] {number_list_path} no existe. Usando número RSA-100 de prueba.")
        numbers = [1522605027922533360535618378132637429718068114961380688657908494580122963258952897654000350692006139]
    else:
        with open(number_list_path) as f:
            numbers = [int(line.strip()) for line in f if line.strip() and line.strip().isdigit()]

    # Reanudación: lo que auto_submit ya verificó y registró en una corrida
    # anterior no se vuelve a atacar.
    already = load_solved_history()
    if already:
        skipped = [n for n in numbers if n in already]
        numbers = [n for n in numbers if n not in already]
        print(f"[*] {len(skipped)} objetivo(s) ya resueltos en corridas anteriores, se saltan.")

    from screening.vulnerability_sweep import sweep
    solved, remaining = sweep(numbers)

    if solved:
        print(f"[+] Barrido resolvió {len(solved)}/{len(numbers)} al instante:")
        for n, factors in solved.items():
            print(f"    {n} = {' * '.join(factors)}")
    print(f"[*] Quedan {len(remaining)} para GNFS/ECM.")

    # De menor a mayor: así el pipeline ataca primero lo más barato de lo
    # que queda, aunque el sweep no lo haya podido resolver del todo.
    remaining.sort()
    return remaining


BACKGROUND_SERVICES = [
    "orchestrator/resource_monitor.py",
    "orchestrator/scoreboard_watcher.py",
    "submitter/auto_submit.py",
]


def _forward_output(name, proc):
    """Reenvía la salida del servicio a la de main.py (y así al log),
    prefijada con su nombre."""
    for line in iter(proc.stdout.readline, ""):
        print(f"[{name}] {line.rstrip()}")


def _launch_service(script):
    name = os.path.splitext(os.path.basename(script))[0]
    # -u: sin buffer, para que la salida llegue según se produce.
    proc = subprocess.Popen([sys.executable, "-u", script], stdout=subprocess.PIPE,
                            stderr=subprocess.STDOUT, text=True, bufsize=1)
    threading.Thread(target=_forward_output, args=(name, proc), daemon=True).start()
    return proc


def start_background_services():
    processes = {}
    print("[*] Levantando servicios en background...")
    for script in BACKGROUND_SERVICES:
        if os.path.exists(script):
            print(f"    -> Iniciando {script}")
            processes[script] = _launch_service(script)
        else:
            print(f"    [!] Aviso: no se encontró {script}. Saltando...")
    return processes


def restart_dead_services(processes):
    """Un servicio caído (p.ej. el submitter) dejaba de entregar resultados
    sin que nadie se enterase durante el resto de la corrida."""
    for script, proc in processes.items():
        if proc.poll() is not None:
            print(f"[!] {script} terminó (código {proc.returncode}); relanzando.")
            processes[script] = _launch_service(script)


def make_anchor_worker(target):
    """Worker principal ("ancla") según el tamaño; ECM corre siempre al lado."""
    if len(str(target)) < GNFS_MIN_DIGITS:
        from workers.yafu_worker import YAFUWorker
        return YAFUWorker()
    from workers.gnfs_worker import GNFSWorker
    return GNFSWorker()


def release(worker):
    """Para el worker y libera lo que deje en disco (el workdir de CADO en
    /tmp: con SIGKILL CADO no limpia, y cada objetivo dejaba sus relaciones)."""
    discard = getattr(worker, "discard_workdir", None)
    if discard:
        discard()
    else:
        worker.kill()


def attack_target(target, cfg, anchor_worker, ecm_worker, loop, max_seconds):
    """Ataca un único objetivo hasta resolverlo o agotar max_seconds.
    Devuelve True si se resolvió."""
    print(f"\n{'='*60}\n[*] OBJETIVO: {target} ({len(str(target))} dígitos) "
          f"-- {anchor_worker.method_name} + ECM\n{'='*60}")

    ecm_worker.start(target, threads=cfg.ecm_min_threads)
    anchor_worker.start(target, threads=cfg.gnfs_min_threads)

    deadline = time.time() + max_seconds
    solved = False
    while time.time() < deadline:
        loop.tick()
        # Resultado en memoria, no glob sobre runtime/: auto_submit borra
        # los factor_found_*.json al procesarlos y el glob podía no verlos.
        for worker in (ecm_worker, anchor_worker):
            if worker.found.is_set():
                print(f"[+] Resuelto por {worker.method_name}: {worker.result}")
                solved = True
        if solved:
            break
        if not anchor_worker.is_running() and not ecm_worker.is_running():
            print("[!] Ambos workers terminaron sin factor -- pasando al siguiente.")
            break
        time.sleep(loop.tick_seconds)

    release(anchor_worker)
    ecm_worker.kill()
    return solved


def run_control_loop(remaining_numbers, seconds_per_target=180, services=None):
    from orchestrator.controller import ControlLoop, Config
    from workers.ecm_worker import ECMWorker
    from benchmark.predict_cost import predict

    if not remaining_numbers:
        print("[+] No hay números restantes para factorizar. ¡Victoria!")
        return

    d_safe = 330
    try:
        t_est, ram_est = predict(100, method="GNFS")
        print(f"[*] Modelo predictivo OK (GNFS 100 dígitos -> {t_est:.1f}s, {ram_est:.1f}MB)")
    except Exception as e:
        print(f"[!] Aviso del modelo predictivo: {e}. Usando D_SAFE fallback de 330 bits.")

    cfg = Config(total_threads=12, ecm_min_threads=2, gnfs_min_threads=6,
                 d_safe_bits=d_safe, ram_critical_pct=90.0, temp_critical_c=85.0)

    print(f"[*] {len(remaining_numbers)} objetivos, atacando de menor a mayor "
          f"({seconds_per_target}s por objetivo antes de pasar al siguiente).")

    resueltos = 0
    for i, target in enumerate(remaining_numbers, 1):
        print(f"\n--- Objetivo {i}/{len(remaining_numbers)} ---")
        if services:
            restart_dead_services(services)
        anchor_worker = make_anchor_worker(target)
        ecm_worker = ECMWorker()
        # El controlador llama "gnfs" al ancla: sus reglas (kill por
        # recursos, kill-switch, varianza) aplican igual a YAFU.
        loop = ControlLoop(cfg, gnfs=anchor_worker, ecm=ecm_worker, tick_seconds=5.0)
        try:
            if attack_target(target, cfg, anchor_worker, ecm_worker, loop, seconds_per_target):
                resueltos += 1
        except KeyboardInterrupt:
            release(anchor_worker)
            ecm_worker.kill()
            print("\n[*] Interrupción detectada. Abortando el resto de objetivos.")
            break

    print(f"\n[+] Resueltos {resueltos}/{len(remaining_numbers)} objetivos restantes.")


def main():
    log_setup.install()
    pre_flight()
    os.makedirs("data", exist_ok=True)
    target_file = "data/target_numbers.txt"

    remaining = phase_zero_cleanup(target_file)
    bg_processes = start_background_services()

    try:
        run_control_loop(remaining, services=bg_processes)
    finally:
        print("[*] Matando servicios en background...")
        for p in bg_processes.values():
            p.terminate()
        for p in bg_processes.values():
            try:
                p.wait(timeout=5)
            except subprocess.TimeoutExpired:
                p.kill()
        print("[+] Shutdown completo. Adiós.")


if __name__ == "__main__":
    main()