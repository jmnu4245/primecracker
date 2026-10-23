"""
targets.py

Lectura de la lista de enteros de reto, compartida por main.py (qué
atacar) y submitter/auto_submit.py (qué se puede entregar).
"""

import os

TARGETS_PATH = os.path.join("data", "target_numbers.txt")


def load_targets(path=TARGETS_PATH):
    """Enteros de reto, uno por línea, en el orden del archivo y sin
    duplicados. Comentarios (#) y líneas que no son dígitos se ignoran.
    None si el archivo no existe."""
    if not os.path.exists(path):
        return None
    with open(path) as f:
        numbers = [int(line.strip()) for line in f if line.strip().isdigit()]
    return list(dict.fromkeys(numbers))
