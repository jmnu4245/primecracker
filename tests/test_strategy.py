import json

import pytest

from orchestrator import strategy as st


# ----------------------- reglas del marcador -----------------------

def test_standing_ordena_de_mayor_a_menor():
    assert st.standing([90, 120, 95]) == (120, 95, 90)

@pytest.mark.parametrize("ours,theirs,expected", [
    ((120,), (110, 105, 100), +1),       # el más largo manda
    ((120, 95), (120, 90), +1),          # empate -> siguiente número
    ((120, 90), (120,), +1),             # tener un número más desempata
    ((110,), (120,), -1),
    ((120, 100), (120, 100), 0),         # empate exacto -> tiempo (desconocido)
    # El rival enseña 3 y coincidimos en ellas: lo que tenga detrás no se ve
    ((120, 100, 90, 80), (120, 100, 90), 0),
    ((120, 100, 90), (120, 100, 95), -1),
])
def test_compare(ours, theirs, expected):
    assert st.compare(ours, theirs) == expected

def test_rank_cuenta_empates_como_medio_puesto():
    rivals = [(130,), (120, 100), (90,)]
    assert st.rank((120, 100), rivals) == pytest.approx(2.5)
    assert st.rank((140,), rivals) == 1.0

def test_needed_to_beat():
    assert st.needed_to_beat([100], (120, 95)) == 120     # quedas (120, 100) > (120, 95)
    assert st.needed_to_beat([90], (120, 95)) == 121      # con (120, 90) no bastaría
    assert st.needed_to_beat([120], (120, 95)) == 96      # empatas el mayor y desempatas
    assert st.needed_to_beat([130], (120,)) is None       # ya vas por delante


# ----------------------- probabilidad -----------------------

def test_p_finish():
    assert st.p_finish(600, 600) == pytest.approx(0.5)
    assert st.p_finish(600, 6000) > 0.99
    assert st.p_finish(6000, 600) < 0.01
    assert st.p_finish(600, 0) == 0.0


# ----------------------- decisión -----------------------

def minutes(table):
    return lambda d: table[d] * 60

def test_sin_rivales_prefiere_el_tamano_mayor_que_cabe():
    est = minutes({80: 1, 100: 17, 120: 300})
    best = st.evaluate_tiers([80] * 10 + [100] * 10 + [120] * 10, [], [], 60 * 60, est)[0]
    assert best.digits == 100            # 120 no cabe en 1 h; 100 sí y vale más que 80

def test_lo_que_no_mejora_el_puesto_no_puntua():
    # Ya tienes un 100; el rival, (110, 95). Otro 100 no cambia nada; un 110
    # te deja en (110, 100) > (110, 95): solo eso sube el puesto.
    est = minutes({100: 17, 110: 45})
    opts = {o.digits: o for o in st.evaluate_tiers([100, 110], [100], [(110, 95)], 3 * 3600, est)}
    assert opts[100].rank_gain == 0
    assert opts[110].rank_gain > 0

def test_un_110_solo_no_basta_contra_110_y_95():
    est = minutes({110: 45})
    opt = st.evaluate_tiers([110], [], [(110, 95)], 3 * 3600, est)[0]
    assert opt.rank_gain == 0         # (110,) pierde ante (110, 95)

def test_si_vas_por_detras_compensa_arriesgar():
    # Rival con 120. Un 100 seguro no sirve para ganar; un 125 con poca
    # probabilidad es lo único que sube puesto.
    est = minutes({100: 17, 125: 600})
    best = st.evaluate_tiers([100, 125], [100], [(120,)], 300 * 60, est)[0]
    assert best.digits == 125 and 0 < best.p_finish < 0.5

def test_si_vas_por_delante_asegura():
    est = minutes({90: 5, 130: 600})
    opts = st.evaluate_tiers([90, 130], [120], [(110,)], 60 * 60, est)
    assert all(o.rank_gain == 0 for o in opts)        # ya eres primero
    assert opts[0].digits == 90                       # mejora segura del desempate


# ----------------------- señales del marcador -----------------------

def test_load_leaderboard(tmp_path):
    path = tmp_path / "state.json"
    path.write_text(json.dumps({"leaderboard": {
        "a": [120, 95], "b": [{"digits": 110, "minute": 12}]}}))
    board = st.load_leaderboard(str(path))
    assert board == {"a": [(120, None), (95, None)], "b": [(110, 12)]}
    assert st.rival_vectors(board) == {"a": (120, 95), "b": (110,)}
    assert st.load_leaderboard(str(tmp_path / "no.json")) == {}

def test_suspicious_tiers():
    est = minutes({110: 45, 100: 17})
    board = {"a": [(110, 3)],            # 3 min frente a 45: casi seguro débil
             "b": [(100, 15)],           # tiempo normal
             "c": [(100, None)]}         # sin minuto: no aporta
    assert st.suspicious_tiers(board, est) == {110}

def test_load_leaderboard_lista_sin_nombres(tmp_path):
    path = tmp_path / "state.json"
    path.write_text(json.dumps({"leaderboard": [[120, 95], [{"digits": 110, "minute": 4}]]}))
    board = st.load_leaderboard(str(path))
    assert board == {"rival 1": [(120, None), (95, None)], "rival 2": [(110, 4)]}
