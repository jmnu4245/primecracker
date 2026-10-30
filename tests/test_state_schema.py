import json
import os
import unittest
from jsonschema import validate
from jsonschema.exceptions import ValidationError

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

class TestStateSchema(unittest.TestCase):
    def setUp(self):
        with open(os.path.join(ROOT, "config/state_schema.json"), "r") as f:
            self.schema = json.load(f)

    @unittest.skipUnless(os.path.exists(os.path.join(ROOT, "state.json")),
                         "state.json no existe (está en .gitignore)")
    def test_valid_state_from_file(self):
        with open(os.path.join(ROOT, "state.json"), "r") as f:
            valid_instance = json.load(f)
        try:
            validate(instance=valid_instance, schema=self.schema)
        except ValidationError:
            self.fail("validate() falló con un state.json válido.")

    def test_estado_minimo_con_marcador_es_valido(self):
        # Sin puntos (el CTF no los tiene): solo tiempo y marcador.
        validate(instance={"time_remaining_seconds": 7200,
                           "leaderboard": {"a": [120, 95], "b": [{"digits": 110, "minute": 12}]}},
                 schema=self.schema)
        validate(instance={}, schema=self.schema)

    def test_marcador_mal_formado(self):
        for bad in ({"leaderboard": {"a": ["120"]}},
                    {"leaderboard": {"a": [{"minute": 3}]}},
                    {"leaderboard": {"a": [0]}}):
            with self.assertRaises(ValidationError):
                validate(instance=bad, schema=self.schema)

    def test_invalid_state_wrong_type(self):
        invalid_instance = {
            "our_score": "cien",
            "enemy_score": 140,
            "time_remaining_seconds": 7200
        }
        with self.assertRaises(ValidationError):
            validate(instance=invalid_instance, schema=self.schema)

    def test_invalid_state_additional_properties(self):
        invalid_instance = {
            "our_score": 150,
            "enemy_score": 140,
            "time_remaining_seconds": 7200,
            "un_campo_inventado": True
        }
        with self.assertRaises(ValidationError):
            validate(instance=invalid_instance, schema=self.schema)

    def test_max_broken_bits_es_aceptado(self):
        # El controlador lo lee para el kill-switch; antes el schema lo
        # rechazaba por additionalProperties: false.
        validate(instance={"our_score": 1, "enemy_score": 2,
                           "time_remaining_seconds": 3, "max_broken_bits": 400},
                 schema=self.schema)

if __name__ == "__main__":
    unittest.main()