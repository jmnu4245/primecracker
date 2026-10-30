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


def phase_zero_cleanup(number_list_path: str):
    """Fase 0: barrido de vulnerabilidades de verdad. Resuelve al instante
    lo que tenga debilidad estructural (primos, potencias perfectas,
    primos cercanos, factor compartido entre módulos). Devuelve (lo que
    necesita los workers, de menor a mayor; lo ya resuelto, en esta Fase 0
    o en corridas anteriores)."""
    log.info("[*] Iniciando limpieza Fase 0 (barrido de vulnerabilidades)...")

    numbers = load_targets(number_list_path)
    if numbers is None:
        log.warning(f"[!] {number_list_path} no existe. Usando número RSA-100 de prueba.")
        numbers = [1522605027922533360535618378132637429718068114961380688657908494580122963258952897654000350692006139]

    # Reanudación: lo que el collector ya verificó y registró en una corrida
    # anterior no se vuelve a atacar.
    already = load_solved_history()
    skipped = [n for n in numbers if n in already]
    if skipped:
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
    return remaining, skipped + [int(n) for n in solved]


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


def method_for_digits(digits):
    """Método del ancla según el tamaño (nombre de la columna method del CSV)."""
    return "YAFU" if digits < SETTINGS.gnfs_min_digits else "CADO-NFS"


def anchor_method(target):
    return method_for_digits(len(str(target)))


def make_anchor_worker(target):
    """Worker principal ("ancla") según el tamaño; ECM corre siempre al lado."""
    if anchor_method(target) == "YAFU":
        from workers.yafu_worker import YAFUWorker
        return YAFUWorker()
    from workers.gnfs_worker import GNFSWorker
    return GNFSWorker()


# Velocidad de esta máquina respecto a la del benchmark (benchmark/machine.py);
# main() la calibra al arrancar. 1.0 = igual que la máquina del benchmark.
MACHINE_FACTOR = 1.0


def estimate_seconds(digits, predictor, anchor_threads):
    """Segundos estimados para un número de `digits` dígitos sin debilidades.
    Sale del benchmark (medido con todos los núcleos de su máquina), escalado
    a esta máquina (MACHINE_FACTOR) y a los hilos que tendrá el ancla
    (suponiendo escalado lineal), más el pretest ECM si toca GNFS. Lanza
    excepción si no hay datos para el método."""
    method = method_for_digits(digits)
    estimate, _ = predictor(digits, method=method)
    estimate *= MACHINE_FACTOR * (os.cpu_count() or anchor_threads) / anchor_threads
    if method == "CADO-NFS" and not SETTINGS.ecm_parallel:
        estimate += SETTINGS.ecm_pretest_seconds
    return estimate


def plan_budget(target, remaining_seconds, predictor, anchor_threads):
    """(segundos para atacar `target` ahora, estimación) -- con None como
    presupuesto si la estimación no cabe en lo que queda: mejor posponerlo
    que gastar el tiempo en algo que no va a terminar."""
    try:
        estimate = estimate_seconds(len(str(target)), predictor, anchor_threads)
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


def ecm_pretest(target, ecm_worker, threads, max_seconds):
    """ECM corto con todos los núcleos antes de GNFS. Devuelve los factores
    si encuentra alguno, o None. Cubre los números "débiles" de CTF (un
    factor de hasta ~30 dígitos) por segundos, frente a los minutos u horas
    que costaría GNFS sobre el número entero."""
    seconds = min(SETTINGS.ecm_pretest_seconds, max_seconds * 0.25)
    if seconds <= 0:
        return None
    log.info(f"[*] Pretest ECM: B1={SETTINGS.ecm_pretest_b1}, "
             f"{SETTINGS.ecm_pretest_curves} curvas, {threads} hilos, máx. {seconds:.0f}s.")
    ecm_worker.TOTAL_CURVES = SETTINGS.ecm_pretest_curves
    ecm_worker.start(target, threads=threads, b1=SETTINGS.ecm_pretest_b1, timeout=int(seconds) + 1)
    deadline = time.time() + seconds
    # busy() y no is_running(): ver BaseWorker.busy
    while ecm_worker.busy() and time.time() < deadline:
        if ecm_worker.found.wait(1):
            break
    ecm_worker.kill()
    if ecm_worker.found.is_set():
        log.info(f"[+] El pretest ECM encontró un factor: {ecm_worker.result}")
        return ecm_worker.result
    log.info("[*] Pretest ECM sin factor: se pasa a GNFS.")
    return None


def attack_target(target, cfg, anchor_worker, ecm_worker, loop, max_seconds, pretest=True):
    """Ataca un único objetivo hasta resolverlo o agotar max_seconds.
    Devuelve los factores encontrados (quizá con compuestos) o None. No
    libera el ancla: lo decide quien llama.

    Por defecto el ancla trabaja sola con todos los núcleos; ante GNFS, la
    precede un pretest de ECM corto. Con ecm_parallel (settings.toml), ECM
    corre junto al ancla todo el tiempo y el controlador reparte hilos."""
    log.info(f"\n{'='*60}\n[*] OBJETIVO: {target} ({len(str(target))} dígitos) "
          f"-- {anchor_worker.method_name}, {max_seconds / 60:.1f} min\n{'='*60}")
    deadline = time.time() + max_seconds

    if SETTINGS.ecm_parallel:
        ecm_worker.start(target, threads=cfg.ecm_min_threads)
    elif pretest and anchor_method(target) == "CADO-NFS" and not getattr(anchor_worker, "snapshot", None):
        # (sin snapshot: si se reanuda en la segunda pasada, el pretest ya se hizo)
        factors = ecm_pretest(target, ecm_worker, cfg.total_threads, max_seconds)
        if factors:
            return factors

    snapshot = getattr(anchor_worker, "snapshot", None)
    if snapshot and os.path.exists(snapshot):
        log.info(f"[*] Reanudando {anchor_worker.method_name} desde la pasada anterior.")
        anchor_worker.start(target, threads=cfg.gnfs_min_threads, resume=snapshot)
    else:
        anchor_worker.start(target, threads=cfg.gnfs_min_threads)

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
        if not anchor_worker.busy() and not ecm_worker.busy():
            log.warning("[!] El ancla terminó sin factor -- pasando al siguiente.")
            break
        time.sleep(loop.tick_seconds)

    ecm_worker.kill()
    return result


class Campaign:
    """Estado compartido de una corrida: plazo, anclas que se pueden
    reanudar y factorizaciones parciales. Lo usan las dos estrategias."""

    def __init__(self, total_seconds, services=None):
        from orchestrator.controller import Config
        self.total, self.anchor_threads, ecm_min = SETTINGS.threads()
        self.base_cfg = Config(total_threads=self.total, ecm_min_threads=ecm_min,
                               gnfs_min_threads=self.anchor_threads,
                               d_safe_bits=SETTINGS.d_safe_bits,
                               ram_critical_pct=SETTINGS.ram_critical_pct,
                               temp_critical_c=SETTINGS.temp_critical_c,
                               cpu_headroom_pct=SETTINGS.cpu_headroom_pct,
                               enemy_kill_switch=SETTINGS.enemy_kill_switch,
                               aimd=SETTINGS.ecm_parallel)
        if SETTINGS.ecm_parallel:
            log.info(f"[*] Hilos: {self.total} en total, {self.anchor_threads} para el ancla, "
                     f"ECM en paralelo desde {ecm_min}.")
        else:
            log.info(f"[*] Hilos: {self.anchor_threads} para el ancla "
                     f"(ECM solo como pretest antes de GNFS).")
        self.deadline_at = time.time() + total_seconds
        self.services = services
        self.anchors = {}     # objetivo -> ancla, para reanudar más tarde
        self.partial = {}     # objetivo -> factores ya encontrados (con compuestos)
        self.pretested = set()

    def remaining(self):
        return self.deadline_at - time.time()

    def attack(self, target, budget):
        """Factores encontrados para target (quizá con compuestos) o None."""
        from orchestrator.controller import ControlLoop
        from workers.ecm_worker import ECMWorker
        if self.services:
            restart_dead_services(self.services)
        if target not in self.anchors:
            self.anchors[target] = make_anchor_worker(target)
        anchor = self.anchors[target]
        ecm_worker = ECMWorker()
        cfg = target_config(self.base_cfg, budget)
        # El controlador llama "gnfs" al ancla: sus reglas (kill por
        # recursos, kill-switch, varianza) aplican igual a YAFU.
        loop = ControlLoop(cfg, gnfs=anchor, ecm=ecm_worker, tick_seconds=5.0)
        try:
            factors = attack_target(target, cfg, anchor, ecm_worker, loop, budget,
                                    pretest=target not in self.pretested)
        finally:
            ecm_worker.kill()
            release(anchor, keep_workdir=True)
        self.pretested.add(target)
        if factors:
            release(self.anchors.pop(target))
        return factors

    def solve(self, target, budget):
        """Factorización completa en primos de target, o None si no se
        consigue en `budget` segundos. Los factores compuestos que deje un
        worker (GNFS da dos factores; ECM, factor y cofactor) se atacan como
        sub-objetivos con el tiempo que quede."""
        from workers.quick_factor import split_to_primes, write_result
        deadline = time.time() + budget
        factors = self.partial.get(target) or self.attack(target, budget)
        if not factors:
            return None
        primes, composites = split_to_primes(factors, seconds=0)
        for composite in composites:
            remaining = deadline - time.time()
            log.info(f"[*] {target}: queda el factor compuesto {composite} "
                     f"({len(str(composite))} dígitos); se ataca como sub-objetivo.")
            sub = self.solve(composite, remaining) if remaining >= SETTINGS.min_target_seconds else None
            if sub is None:
                # Lo encontrado no se pierde: un intento posterior parte de aquí.
                self.partial[target] = primes + list(composites)
                return None
            primes += sub
        primes.sort()
        if composites:
            # Lo que escribió el worker era parcial (el collector lo apartó):
            # se escribe la factorización completa para que se registre.
            write_result(target, primes, "Combinado (sub-objetivos)", time.time() - deadline + budget)
        return primes

    def hunt(self, targets):
        """Pretest ECM sobre cada número de un grupo del mismo tamaño, para
        dar con el débil antes de comprometer GNFS a uno cualquiera.
        Devuelve los que quedan resueltos."""
        from workers.ecm_worker import ECMWorker
        found = []
        for target in targets:
            remaining = self.remaining()
            if remaining < SETTINGS.min_target_seconds:
                break
            factors = ecm_pretest(target, ECMWorker(), self.total, remaining)
            self.pretested.add(target)
            if factors:
                self.partial[target] = factors
                if self.solve(target, self.remaining()) is not None:
                    found.append(target)
        return found

    def release_all(self):
        for anchor in self.anchors.values():
            release(anchor)


def run_count(campaign, numbers, predictor, already_solved=()):
    """Estrategia 'count' (CTF que puntúan por cantidad): de menor a mayor,
    con presupuesto por coste estimado, y una segunda pasada para lo
    pospuesto o no resuelto. Ordenar por menor coste maximiza el número de
    resueltos con un plazo común (regla SPT)."""
    solved = set()
    retry = []
    log.info("\n[*] Primera pasada")
    for target in sorted(numbers):
        remaining = campaign.remaining()
        budget, estimate = plan_budget(target, remaining, predictor, campaign.anchor_threads)
        if budget is None:
            log.info(f"[*] {target} ({len(str(target))} dígitos): estimado "
                     f"{estimate / 60:.0f} min, no cabe en {remaining / 60:.0f} min -- pospuesto.")
            retry.append(target)
            continue
        if remaining < SETTINGS.min_target_seconds or campaign.solve(target, budget) is None:
            retry.append(target)
        else:
            solved.add(target)

    log.info("\n[*] Segunda pasada")
    for target in sorted(retry):
        remaining = campaign.remaining()
        if remaining < SETTINGS.min_target_seconds:
            log.info("[*] Sin tiempo para más objetivos.")
            break
        if campaign.solve(target, remaining) is not None:
            solved.add(target)
    return solved


# Cazar el débil de un grupo compensa si cuesta menos que esta fracción
# del GNFS que se ahorraría (con el marcador señalando un débil, siempre).
HUNT_MAX_COST_RATIO = 1 / 3


def _log_plan(options, ours, rivals):
    from orchestrator import strategy
    current = strategy.standing(ours)
    log.info(f"\n[estrategia] Tu marcador: {current or '(nada roto)'}; "
             f"puesto esperado {strategy.rank(current, list(rivals.values())):.1f} "
             f"de {len(rivals) + 1}.")
    for team, theirs in sorted(rivals.items(), key=lambda kv: kv[1], reverse=True):
        need = strategy.needed_to_beat(ours, theirs)
        if need is not None:
            log.info(f"[estrategia] Para superar a {team} {theirs} necesitas romper "
                     f"al menos un número de {need} dígitos.")
    for o in options[:5]:
        log.info(f"[estrategia]   {o.digits} dígitos ×{o.count}: ~{o.estimate / 60:.0f} min, "
                 f"P(terminar)={o.p_finish:.2f}, puestos +{o.rank_gain:.2f}"
                 f"{', marcador sugiere uno débil' if o.suspicious else ''}")


def run_longest(campaign, numbers, predictor, already_solved=()):
    """Estrategia 'longest' (gana quien rompe el número más largo; empate,
    el siguiente; empate en todo, el tiempo). Tras cada número resuelto se
    vuelve a decidir con el marcador de state.json: el tamaño con mayor
    mejora esperada de puesto (orchestrator/strategy.py). Antes de dedicar
    GNFS a un grupo de números del mismo tamaño, se caza el débil con un
    pretest ECM por cada uno."""
    from orchestrator import strategy
    # Lo ya roto (Fase 0 y corridas anteriores) cuenta en el marcador propio.
    ours = [len(str(n)) for n in already_solved]
    pending = list(numbers)
    solved = set()
    exhausted = set()     # tamaños que no se pueden terminar en el tiempo que queda
    hunted = set()

    def estimator(digits):
        try:
            return estimate_seconds(digits, predictor, campaign.anchor_threads)
        except Exception:
            return SETTINGS.default_target_seconds

    while pending:
        remaining = campaign.remaining()
        if remaining < SETTINGS.min_target_seconds:
            log.info("[*] Sin tiempo para más objetivos.")
            break
        board = strategy.load_leaderboard()
        rivals = strategy.rival_vectors(board)
        options = strategy.evaluate_tiers(
            [len(str(t)) for t in pending if len(str(t)) not in exhausted],
            ours, list(rivals.values()), remaining, estimator,
            strategy.suspicious_tiers(board, estimator))
        if not options:
            log.info("[*] No queda ningún tamaño que se pueda terminar a tiempo.")
            break
        _log_plan(options, ours, rivals)
        best = options[0]
        tier = [t for t in pending if len(str(t)) == best.digits]

        hunt_cost = len(tier) * SETTINGS.ecm_pretest_seconds
        if (best.digits not in hunted and len(tier) > 1
                and method_for_digits(best.digits) == "CADO-NFS"
                and (best.suspicious or hunt_cost < HUNT_MAX_COST_RATIO * best.estimate)):
            hunted.add(best.digits)
            log.info(f"[estrategia] Caza del débil: pretest ECM a los {len(tier)} números "
                     f"de {best.digits} dígitos antes de comprometer GNFS.")
            for target in campaign.hunt(tier):
                pending.remove(target)
                solved.add(target)
                ours.append(best.digits)
            continue

        # Dentro del grupo, primero el que ya tenga trabajo hecho (parcial o
        # criba de GNFS que reanudar).
        tier.sort(key=lambda t: (t not in campaign.partial, t not in campaign.anchors))
        target = tier[0]
        if campaign.solve(target, remaining) is not None:
            pending.remove(target)
            solved.add(target)
            ours.append(best.digits)
        else:
            # Abortado (la ETA no llegaba) o sin tiempo: los demás del grupo
            # son igual de duros, se descarta el tamaño y se vuelve a decidir.
            log.info(f"[estrategia] {best.digits} dígitos no se termina a tiempo; se descarta.")
            exhausted.add(best.digits)
    return solved


def run_control_loop(remaining_numbers, total_seconds, services=None, already_solved=()):
    from benchmark.predict_cost import predict

    if not remaining_numbers:
        log.info("[+] No hay números restantes para factorizar. ¡Victoria!")
        return
    log.info(f"[*] {len(remaining_numbers)} objetivos, {total_seconds / 60:.0f} min disponibles, "
             f"estrategia '{SETTINGS.strategy}'.")
    campaign = Campaign(total_seconds, services)
    run = run_longest if SETTINGS.strategy == "longest" else run_count
    solved = set()
    try:
        solved = run(campaign, remaining_numbers, predict, already_solved)
    except KeyboardInterrupt:
        log.info("\n[*] Interrupción detectada. Abortando el resto de objetivos.")
    finally:
        campaign.release_all()
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
    global MACHINE_FACTOR
    from benchmark.machine import ensure_factor
    MACHINE_FACTOR = ensure_factor()
    os.makedirs("data", exist_ok=True)
    target_file = "data/target_numbers.txt"
    total_seconds = resolve_total_seconds(args.minutes)

    remaining, already_solved = phase_zero_cleanup(target_file)
    bg_processes = start_background_services()

    try:
        run_control_loop(remaining, total_seconds, services=bg_processes,
                         already_solved=already_solved)
    finally:
        log.info("[*] Matando servicios en background...")
        for p in bg_processes.values():
            p.terminate()
        for p in bg_processes.values():
            try:
                p.wait(timeout=5)
            except subprocess.TimeoutExpired:
                p.kill()
        flush_pending_results()
        log.info("[+] Shutdown completo. Adiós.")


def flush_pending_results():
    """Registra lo que quede en runtime/ sin procesar. Un resultado escrito
    justo antes del apagado (p.ej. la factorización combinada de un
    sub-objetivo) llegaba después de parar el collector y no aparecía en
    para_entregar.txt hasta la siguiente corrida. Se hace con el collector
    ya parado, para que no lo procesen dos procesos a la vez."""
    from submitter.collector import FactorCollector
    FactorCollector().process_existing(RUNTIME_DIR)


if __name__ == "__main__":
    main()
