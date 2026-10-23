from targets import load_targets


def test_load_targets_ignora_comentarios_y_duplicados(tmp_path):
    path = tmp_path / "t.txt"
    path.write_text("# retos\n10403\n\n  15  \n10403\nabc\n-7\n")
    assert load_targets(str(path)) == [10403, 15]

def test_load_targets_sin_archivo(tmp_path):
    assert load_targets(str(tmp_path / "no.txt")) is None
