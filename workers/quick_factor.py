"""
workers/quick_factor.py

Métodos baratos de factorización, en Python y CPU: primalidad
(Miller-Rabin), potencia perfecta, trial division, Pollard p-1 y rho, y
split_to_primes para completar en primos lo que den otros métodos.
run_cleanup() es la pasada rápida por número de la Fase 0.

(Antes gpu_cleanup.py: nunca usó la GPU. Portarlo no compensa: estos
métodos tardan milisegundos o pocos segundos, y lo caro -- ECM, SIQS,
GNFS -- ya lo hacen herramientas optimizadas.)
"""

import logging
import math
import json
import os
import time
import random

log = logging.getLogger(__name__)

os.makedirs("runtime", exist_ok=True)


def is_probable_prime(n, rounds=40):
    """Miller-Rabin. Determinista para n < 3.3e24 con el set de testigos
    conocido; probabilístico (error < 4^-rounds) por encima, porque los
    números de un CTF pueden tener cientos de dígitos."""
    if n < 2:
        return False
    small_primes = (2, 3, 5, 7, 11, 13, 17, 19, 23, 29, 31, 37)
    for p in small_primes:
        if n == p:
            return True
        if n % p == 0:
            return False

    d, r = n - 1, 0
    while d % 2 == 0:
        d //= 2
        r += 1

    if n < 3_317_044_064_679_887_385_961_981:
        witnesses = small_primes
    else:
        witnesses = [random.randrange(2, n - 1) for _ in range(rounds)]

    for a in witnesses:
        if a >= n:
            continue
        x = pow(a, d, n)
        if x == 1 or x == n - 1:
            continue
        for _ in range(r - 1):
            x = pow(x, 2, n)
            if x == n - 1:
                break
        else:
            return False
    return True


def trial_division(n, limit=100000):
    if n % 2 == 0:
        return 2
    for i in range(3, limit, 2):
        if n % i == 0:
            return i
    return None


def pollard_p_minus_1(n, b=100000):
    a = 2
    for j in range(2, b):
        a = pow(a, j, n)
        d = math.gcd(a - 1, n)
        if 1 < d < n:
            return d
    return None


def integer_nth_root(n, k):
    """Raíz entera k-ésima exacta de n, o None si no es exacta."""
    if n <= 0:
        return None
    lo, hi = 1, 1 << ((n.bit_length() // k) + 2)
    while lo < hi:
        mid = (lo + hi + 1) // 2
        if mid ** k <= n:
            lo = mid
        else:
            hi = mid - 1
    return lo if lo ** k == n else None


def check_perfect_power(n, max_exponent=64):
    """Detecta N = p^k para 2 <= k <= max_exponent."""
    for k in range(2, max_exponent + 1):
        root = integer_nth_root(n, k)
        if root is not None and root > 1:
            return root, k
    return None


def pollard_rho(n, seconds=2.0):
    """Pollard rho (variante de Brent). Encuentra en segundos factores de
    hasta ~20 dígitos, donde trial division ya no llega. None si no da con
    ninguno antes de `seconds`."""
    if n % 2 == 0:
        return 2
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        y, c, m = random.randrange(1, n), random.randrange(1, n), 128
        g = r = q = 1
        x = ys = y
        while g == 1:
            x = y
            for _ in range(r):
                y = (y * y + c) % n
            k = 0
            while k < r and g == 1:
                if time.monotonic() > deadline:
                    return None
                ys = y
                for _ in range(min(m, r - k)):
                    y = (y * y + c) % n
                    q = q * abs(x - y) % n
                g = math.gcd(q, n)
                k += m
            r *= 2
        if g == n:
            # El producto acumulado se pasó: rehacer paso a paso.
            g = 1
            while g == 1:
                ys = (ys * ys + c) % n
                g = math.gcd(abs(x - ys), n)
        if 1 < g < n:
            return g
    return None


def split_to_primes(factors, seconds=5.0):
    """Descompone los factores compuestos con métodos baratos (potencia
    perfecta, trial division, Pollard rho) hasta agotar `seconds`.
    Devuelve (primos, compuestos_que_quedan), ambos ordenados. Con
    seconds=0 solo clasifica (y separa potencias perfectas, que es barato).

    Hace falta porque muchos métodos dan un factor y su cofactor sin mirar
    si el cofactor es primo: p.ej. trial division sobre 10^42+57 daba
    14923 * (un compuesto de 38 dígitos)."""
    deadline = time.monotonic() + seconds
    primes, composites = [], []
    stack = [int(f) for f in factors]
    while stack:
        f = stack.pop()
        if f == 1:
            continue
        if is_probable_prime(f):
            primes.append(f)
            continue
        power = check_perfect_power(f)
        if power:
            stack += [power[0]] * power[1]
            continue
        divisor = None
        remaining = deadline - time.monotonic()
        if remaining > 0:
            divisor = trial_division(f, limit=10000) or pollard_rho(f, seconds=remaining)
        if divisor:
            stack += [divisor, f // divisor]
        else:
            composites.append(f)
    return sorted(primes), sorted(composites)


def run_cleanup(n):
    """Devuelve:
        "PRIME"      si n es primo (nada que factorizar)
        (p, q)       si se encontró un factor
        None         si es compuesto pero ninguno de estos métodos baratos lo resolvió
    """
    log.info(f"[cleanup] Analizando {n}...")
    start_time = time.time()

    if is_probable_prime(n):
        elapsed = time.time() - start_time
        log.info(f"[-] {n} es primo (Miller-Rabin, {elapsed:.4f}s). Nada que factorizar.")
        return "PRIME"

    factor = trial_division(n)
    method = "Trial Division"

    if not factor:
        factor = pollard_p_minus_1(n)
        method = "Pollard p-1"

    if factor:
        elapsed = time.time() - start_time
        log.info(f"[+] {method} encontró factor: {factor} ({elapsed:.2f}s)")
        # Sin escribir resultado aquí: el cofactor puede ser compuesto, y es
        # quien llama (sweep) quien decide si la factorización está completa.
        return factor, n // factor

    log.info("[-] No se encontraron factores pequeños ni p-1-suaves.")
    return None


def write_result(n, factors, method, elapsed):
    """Deja el resultado en runtime/ para que el collector lo registre.
    Lo usan también los métodos de screening/vulnerability_sweep.py."""
    payload = {
        "target_id": "auto_cleanup",
        "n": str(n),
        "factors": [str(f) for f in factors],
        "complete": not split_to_primes(factors, seconds=0)[1],
        "method": method,
        "time_elapsed_seconds": round(elapsed, 4),
    }
    # Nombre único por número: quick_factor, ecm_worker y gnfs_worker pueden
    # encontrar factores casi a la vez y no deben pisarse el mismo archivo.
    out_path = f"runtime/factor_found_cleanup_{n}.json"
    tmp_path = out_path + ".tmp"
    with open(tmp_path, "w") as f:
        json.dump(payload, f, indent=2)
    os.replace(tmp_path, out_path)  # escritura atómica


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    test_numbers = [5959, 10967434867, 999999937]
    for num in test_numbers:
        run_cleanup(num)
        log.info("-" * 30)