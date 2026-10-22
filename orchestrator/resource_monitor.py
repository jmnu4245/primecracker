import psutil
import json
import time
import os
import platform

os.makedirs("runtime", exist_ok=True)

def get_temperature():
    if platform.system() == "Linux" and hasattr(psutil, "sensors_temperatures"):
        temps = psutil.sensors_temperatures()
        if temps:
            for name, entries in temps.items():
                for entry in entries:
                    return entry.current
    # En Windows psutil no lee temperatura nativamente. 
    return 0.0

def main():
    print("[*] Iniciando Resource Monitor...")
    while True:
        data = {
            "cpu_percent": psutil.cpu_percent(interval=1),
            "ram_percent": psutil.virtual_memory().percent,
            "temperature": get_temperature(),
            "last_updated": time.time()
        }
        tmp_file = "runtime/resources.json.tmp"
        final_file = "runtime/resources.json"
        
        with open(tmp_file, "w") as f:
            json.dump(data, f, indent=2)
        os.replace(tmp_file, final_file)
        
        time.sleep(2)

if __name__ == "__main__":
    main()