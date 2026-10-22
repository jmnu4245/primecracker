"""
workers/gpu_cleanup.py

Fase de limpieza rápida por número: trial division + Pollard p-1.

NOTA HONESTA: pese al nombre heredado de la arquitectura original, esto
corre en CPU puro (no usa la GTX 1650). Si quieres que la GPU aporte de
verdad, esto necesitaría portarse a algo vectorizado (p.ej. cupy para la
trial division). Tal cual está, preúsalo como filtro CPU barato y
preséntalo así en tu presupuesto de hilos, no como "gratis".
"""

import math
import json
import os
import time
import random

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


def run_cleanup(n):
    """Devuelve:
        "PRIME"      si n es primo (nada que factorizar)
        (p, q)       si se encontró un factor
        None         si es compuesto pero ninguno de estos métodos baratos lo resolvió
    """
    print(f"[*] GPU_Cleanup analizando {n}...")
    start_time = time.time()

    if is_probable_prime(n):
        elapsed = time.time() - start_time
        print(f"[-] {n} es primo (Miller-Rabin, {elapsed:.4f}s). Nada que factorizar.")
        return "PRIME"

    factor = trial_division(n)
    method = "Trial Division"

    if not factor:
        factor = pollard_p_minus_1(n)
        method = "Pollard p-1"

    if factor:
        elapsed = time.time() - start_time
        print(f"[+] {method} encontró factor: {factor}")
        write_result(n, [factor, n // factor], method, elapsed)
        return factor, n // factor

    print("[-] No se encontraron factores pequeños ni p-1-suaves.")
    return None


def write_result(n, factors, method, elapsed):
    """Deja el resultado en runtime/ para que auto_submit lo entregue.
    Lo usan también los métodos de screening/vulnerability_sweep.py."""
    payload = {
        "target_id": "auto_cleanup",
        "n": str(n),
        "factors": [str(f) for f in factors],
        "method": method,
        "time_elapsed_seconds": round(elapsed, 4),
    }
    # Nombre único por número: gpu_cleanup, ecm_worker y gnfs_worker pueden
    # encontrar factores casi a la vez y no deben pisarse el mismo archivo.
    out_path = f"runtime/factor_found_cleanup_{n}.json"
    tmp_path = out_path + ".tmp"
    with open(tmp_path, "w") as f:
        json.dump(payload, f, indent=2)
    os.replace(tmp_path, out_path)  # escritura atómica


if __name__ == "__main__":
    test_numbers = [5959, 10967434867, 999999937]
    for num in test_numbers:
        run_cleanup(num)
        print("-" * 30)