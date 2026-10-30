"""
orchestrator/strategy.py

Qué atacar, según las reglas del CTF:

- Gana quien rompe el número MÁS LARGO; si hay empate, se mira el
  siguiente más largo, y así sucesivamente; con empate en todos, gana
  quien llegó antes.
- El marcador muestra de cada equipo sus 2-3 longitudes mayores.

Es decir, cada equipo es un vector de longitudes rotas ordenado de mayor a
menor, comparado lexicográficamente ((120, 95) > (120, 90) > (120,)). Con
eso, para cada tamaño de número que queda por romper:

    utilidad = P(terminarlo a tiempo) × (puestos que se ganan si se rompe)

con la mejora esperada del propio vector como desempate. Un tamaño que no
mejora el puesto (porque el rival ya tiene algo mayor) tiene utilidad de
puesto 0: solo sirve de relleno o desempate.

Todo son funciones puras: main.py les pasa el estado y el estimador.
"""

import json
import math
from dataclasses import dataclass

# El marcador muestra hasta este número de longitudes por equipo. Si un
# rival enseña SHOWN y coincidimos en ellas, lo que tenga detrás no se ve.
SHOWN = 3
# Error del modelo de coste medido en el benchmark (hasta ~30%): sigma de
# ln(T) para la distribución log-normal del tiempo real.
COST_SIGMA = 0.3
# Un rival que rompe un tamaño en menos de esta fracción de lo que costaría
# con GNFS en nuestra máquina probablemente encontró un número débil (con
# margen de sobra para que no sea solo que tiene más hardware).
WEAK_SIGNAL_RATIO = 0.1


def standing(lengths):
    """Vector de un equipo: longitudes rotas de mayor a menor."""
    return tuple(sorted((int(d) for d in lengths), reverse=True))


def compare(ours, theirs, shown=SHOWN):
    """+1 si `ours` va por delante, -1 si por detrás, 0 si empate (o si no
    se puede saber: el rival enseña `shown` longitudes y coinciden)."""
    if len(theirs) >= shown:
        ours = ours[:len(theirs)]
    return (ours > theirs) - (ours < theirs)


def rank(ours, rivals, shown=SHOWN):
    """Puesto esperado (1 = primero). Empate exacto = medio puesto: gana
    quien llegó antes y no sabemos quién fue."""
    position = 1.0
    for theirs in rivals:
        c = compare(ours, theirs, shown)
        position += 1.0 if c < 0 else 0.5 if c == 0 else 0.0
    return position


def lex_value(vector):
    """Número real que respeta el orden lexicográfico (longitudes < 1000)."""
    return sum(d / 1000.0 ** i for i, d in enumerate(vector))


def p_finish(estimate_seconds, available_seconds, sigma=COST_SIGMA):
    """P(terminar en `available_seconds`) si el tiempo real es log-normal
    alrededor de la estimación."""
    if available_seconds <= 0:
        return 0.0
    if estimate_seconds <= 0:
        return 1.0
    z = (math.log(available_seconds) - math.log(estimate_seconds)) / sigma
    return 0.5 * (1.0 + math.erf(z / math.sqrt(2.0)))


@dataclass
class TierOption:
    digits: int
    count: int              # números de ese tamaño que quedan
    estimate: float         # segundos estimados (número sin debilidad)
    p_finish: float
    rank_gain: float        # puestos ganados esperados
    lex_gain: float         # mejora esperada del propio vector
    suspicious: bool        # el marcador sugiere que hay uno débil


def evaluate_tiers(candidate_digits, ours, rivals, remaining_seconds, estimator,
                   suspicious=frozenset()):
    """Opciones por tamaño, de mejor a peor. `candidate_digits`: longitud
    de cada número pendiente; `ours`: longitudes ya rotas; `rivals`:
    vectores de los rivales; `estimator(d)`: segundos para un número de d
    dígitos sin debilidades."""
    current = standing(ours)
    current_rank = rank(current, rivals)
    counts = {}
    for d in candidate_digits:
        counts[d] = counts.get(d, 0) + 1
    options = []
    for d, count in counts.items():
        estimate = estimator(d)
        p = p_finish(estimate, remaining_seconds)
        new = standing(list(ours) + [d])
        options.append(TierOption(
            digits=d, count=count, estimate=estimate, p_finish=p,
            rank_gain=p * (current_rank - rank(new, rivals)),
            lex_gain=p * (lex_value(new) - lex_value(current)),
            suspicious=d in suspicious))
    # Primero el puesto; después el propio vector. Entre iguales, el más
    # probable (y así, a igualdad, el que antes termina: desempate por tiempo).
    options.sort(key=lambda o: (o.rank_gain, o.lex_gain, o.p_finish), reverse=True)
    return options


def needed_to_beat(ours, theirs, max_digits=999, shown=SHOWN):
    """Menor longitud que, rompiéndola, deja a `ours` por delante de
    `theirs`; None si ninguna basta (o ya vamos por delante)."""
    if compare(standing(ours), theirs, shown) > 0:
        return None
    for d in range(1, max_digits + 1):
        if compare(standing(list(ours) + [d]), theirs, shown) > 0:
            return d
    return None


def suspicious_tiers(leaderboard, estimator, ratio=WEAK_SIGNAL_RATIO):
    """Tamaños que algún rival rompió mucho más rápido de lo que costaría
    sin debilidad: probablemente hay un número débil en ese grupo."""
    flagged = set()
    for entries in leaderboard.values():
        for digits, minute in entries:
            if minute is not None and minute * 60 < ratio * estimator(digits):
                flagged.add(digits)
    return flagged


def load_leaderboard(path="state.json"):
    """{equipo: [(dígitos, minuto o None), ...]} desde state.json. Cada
    entrada puede ser un entero o {"digits": d, "minute": m}. Sin archivo o
    sin marcador, {}."""
    try:
        with open(path) as f:
            raw = json.load(f).get("leaderboard", {})
    except (OSError, ValueError, AttributeError):
        return {}
    board = {}
    for team, entries in raw.items():
        parsed = []
        for entry in entries:
            if isinstance(entry, dict):
                parsed.append((int(entry["digits"]), entry.get("minute")))
            else:
                parsed.append((int(entry), None))
        board[team] = parsed
    return board


def rival_vectors(leaderboard):
    return {team: standing(d for d, _ in entries) for team, entries in leaderboard.items()}
