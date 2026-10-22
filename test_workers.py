import time
import os
import glob
from workers.ecm_worker import ECMWorker
from workers.yafu_worker import YAFUWorker
from workers.gnfs_worker import GNFSWorker

os.makedirs("runtime", exist_ok=True)

def test_worker(worker_name, worker_class, target_n):
    print(f"\n{'='*40}")
    print(f"[*] Testeando {worker_name}")
    print(f"{'='*40}")
    
    worker = worker_class()
    
    # 1. Probar arranque
    print(f"[*] start({target_n}, threads=2)")
    worker.start(target_n, threads=2)
    time.sleep(2) # Dar tiempo a que el subproceso se asiente
    
    # 2. Probar estado
    if worker.is_running():
        print("[+] is_running(): True (Proceso ejecutándose correctamente)")
        
        # 3. Probar redimensión (Placeholder por ahora)
        print("[*] resize(4)")
        worker.resize(4)
        
        print("[*] Dejando correr 5 segundos...")
        time.sleep(5)
        
        # 4. Probar interrupción
        print("[*] kill()")
        worker.kill()
        time.sleep(1)
        
        if not worker.is_running():
            print("[+] is_running(): False (Proceso matado con éxito)")
        else:
            print("[-] Error: El proceso sigue activo tras kill()")
    else:
        print("[-] Error o fin prematuro: El proceso no está corriendo.")
        
    # Verificar si por casualidad escupió algún JSON de éxito
    for f in glob.glob(f"runtime/factor_found_*{target_n}*.json"):
        print(f"[+] ¡Factor encontrado rápido! Guardado en: {f}")

if __name__ == "__main__":
    # Limpiar estado previo
    for f in glob.glob("runtime/gnfs_progress.json*"):
        os.remove(f)

    # Números grandes arbitrarios para que no factoricen en 1 milisegundo y nos de tiempo a probar kill()
# RSA-100 (Semiprimo estricto, no tiene divisores pequeños)
    rsa_100 = 1522605027922533360535618378132637429718068114961380688657908494580122963258952897654000350692006139

    n_ecm = rsa_100
    n_yafu = rsa_100
    n_gnfs = rsa_100

    test_worker("ECMWorker", ECMWorker, n_ecm)
    test_worker("YAFUWorker", YAFUWorker, n_yafu)
    test_worker("GNFSWorker", GNFSWorker, n_gnfs)

    print("\n[+] Tests finalizados.")