import json
import time
import os
from jsonschema import validate, ValidationError

os.makedirs("runtime", exist_ok=True)

SCHEMA = {}
if os.path.exists("config/state_schema.json"):
    with open("config/state_schema.json", "r") as f:
        SCHEMA = json.load(f)

def main():
    print("[*] Iniciando State Watcher...")
    while True:
        try:
            if os.path.exists("state.json"):
                with open("state.json", "r") as f:
                    data = json.load(f)
                
                if SCHEMA:
                    validate(instance=data, schema=SCHEMA)
                
                data["last_updated"] = time.time()
                
                tmp_file = "runtime/enemy_state.json.tmp"
                final_file = "runtime/enemy_state.json"
                with open(tmp_file, "w") as f:
                    json.dump(data, f, indent=2)
                os.replace(tmp_file, final_file)
                
        except (json.JSONDecodeError, ValidationError) as e:
            print(f"Error de validación/lectura: {e}")
        
        time.sleep(2)

if __name__ == "__main__":
    main()