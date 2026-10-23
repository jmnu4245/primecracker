# primecracker

**Orquestador de factorización de enteros para retos CTF de matemática/criptografía.**

Este proyecto es una herramienta **educativa y de competición**. Resuelve retos de
factorización de los que se plantean en competiciones *Capture The Flag* (CTF) y en
cursos de teoría de números: dado un entero compuesto `N` publicado como reto, encontrar
sus factores primos.

No ataca sistemas, redes ni claves de terceros. Todos los "objetivos" son **números de
reto**, es decir, enteros publicados a propósito para ser factorizados dentro de una
competición o de un ejercicio. Los casos que resuelve (primos generados demasiado cerca,
módulos que comparten un factor, potencias perfectas) son los ejercicios didácticos
clásicos sobre *por qué una mala generación de claves es insegura* — se estudian
precisamente para aprender a generar claves correctamente.

## Glosario (para evitar malentendidos)

El código usa vocabulario de competición. Significa esto y solo esto:

| Término en el código | Significado real |
|----------------------|------------------|
| `target` / objetivo  | Un entero de reto a factorizar, de `data/target_numbers.txt`. |
| `enemy` / enemigo    | El equipo rival **en el marcador del CTF**. Nunca una máquina ni una persona a atacar. |
| `scoreboard`         | El marcador público de la competición. |
| `vulnerability_sweep`| Barrido de **debilidades matemáticas conocidas** del propio número de reto (Fermat, GCD, potencia perfecta), para resolverlo rápido sin gastar cómputo pesado. |
| `attack` / atacar    | Lanzar los factorizadores (ECM, GNFS) contra un número de reto. |
| `collector`          | Recoge y valida los resultados para entregarlos **a mano** (no envía nada a ningún sitio). |

## Arquitectura

Pipeline por fases con un bucle de control central (`main.py`):

1. **Pre-flight** (`build/setup_toolchain.sh`): verifica la toolchain compilada
   (CADO-NFS, GMP-ECM, YAFU) y enlaza en `bin/active/` la variante que funciona
   en este CPU.
2. **Fase 0 — barrido** (`screening/vulnerability_sweep.py`): debilidades
   estructurales en segundos (GCD cruzado, potencia perfecta, Fermat, trial
   division, Pollard p-1 y rho). Solo da por resuelto lo que queda **completo en
   primos**; lo demás pasa a los workers.
3. **Servicios en background**: monitor de recursos, observador del marcador y
   recolector de resultados (`orchestrator/`, `submitter/`). Si uno cae, se
   relanza al empezar el siguiente objetivo.
4. **Reparto del tiempo** (`main.py`): cada objetivo recibe un presupuesto según
   su coste estimado (`benchmark/predict_cost.py`); lo que no cabe se pospone.
   Una segunda pasada reintenta lo pospuesto y lo no resuelto con el tiempo que
   quede, reanudando GNFS desde su criba.
5. **Bucle de control** (`orchestrator/controller.py`): reparte hilos entre el
   ancla y ECM (AIMD), y la para si hay RAM/temperatura críticas, si el rival
   supera `d_safe_bits` o si la ETA de la criba de CADO no llega al plazo.
6. **Workers** (`workers/`): adaptadores sobre las herramientas CLI, cada uno
   en su propio directorio de trabajo bajo `runtime/`.

Por objetivo, el "ancla" se elige por tamaño: **YAFU (SIQS)** por debajo de
`gnfs_min_digits` y **CADO-NFS (GNFS)** desde ahí; GMP-ECM corre siempre en
paralelo. Si un worker devuelve un factor compuesto, se ataca como
sub-objetivo y se registra la factorización combinada.

## Uso

```bash
pip install -r requirements.txt     # Python >= 3.11
# Coloca los enteros de reto, uno por línea, en data/target_numbers.txt
python main.py                      # tiempo: time_remaining_seconds de state.json
python main.py --minutes 45         # o explícito
python main.py --log-level WARNING  # consola más callada (el archivo lo guarda todo)
```

- **Configuración**: `config/settings.toml` (hilos, límites, umbral YAFU/GNFS,
  reparto del tiempo, ruta de la toolchain). Todo opcional; una clave mal
  escrita hace fallar el arranque.
- **Log**: `runtime/primecracker.log`, con fecha, nivel y módulo por línea
  (incluidos los servicios y las excepciones no controladas).
- **Entrega (manual)**: la entrega es por Outlook con la cuenta de la
  organización y no se puede automatizar. Cada resultado válido se anota en
  **`runtime/para_entregar.txt`** (número y factores, listo para copiar) y se
  anuncia en consola como `[ENTREGAR A MANO]`. Solo se anotan factorizaciones
  completas en primos de números que estén en `data/target_numbers.txt` (se
  relee cada vez). Lo parcial se aparta
  en `runtime/partial/` y lo que no es objetivo en `runtime/ignored/`.
- **Reanudación**: lo ya registrado queda en `runtime/solved_history.jsonl`
  y no se vuelve a atacar en la siguiente corrida.

## Benchmark

```bash
python -m benchmark.run_benchmark --start 60 --stop 100 --step 5 \
    --methods YAFU CADO-NFS --threads 12 --timeout 3600
```

Añade filas a `benchmark_results.csv`, de donde sale la estimación de coste.
Mide con todos los núcleos (`--threads`), sin contar el tiempo que el equipo
esté suspendido.

## Tests

```bash
pip install -r requirements-dev.txt
pytest -q                          # todo (≈10 s)
pytest -q -m "not integration"     # sin los binarios reales de bin/active (como en CI)
python -m scripts.smoke_workers    # prueba manual de los 3 binarios con RSA-100
```

## Ámbito y uso aceptable

- Pensado para **tus propios retos**: CTFs en los que participas, ejercicios de curso,
  o números que tú mismo generas para practicar.
- No introduzcas aquí módulos de criptografía de terceros ni datos que no tengas
  autorización explícita para procesar.
