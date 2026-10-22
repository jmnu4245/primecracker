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
| `auto_submit`        | Entrega del resultado. En este repo está **simulada** (imprime y registra; no envía nada a ningún servicio externo). |

## Arquitectura

Pipeline por fases con un bucle de control central (`main.py`):

1. **Pre-flight** (`build/setup_toolchain.sh`): verifica la toolchain compilada
   (CADO-NFS, GMP-ECM, YAFU) en `bin/active/`.
2. **Fase 0 — barrido** (`screening/vulnerability_sweep.py`): detecta debilidades
   estructurales en segundos (GCD cruzado, potencia perfecta, Fermat, trial division +
   Pollard p-1). Lo resuelto se aparta; el resto se ordena de menor a mayor.
3. **Servicios en background**: monitor de recursos, observador del marcador y
   entregador de resultados (`orchestrator/`, `submitter/`).
4. **Bucle de control** (`orchestrator/controller.py`): reparte hilos entre los
   factorizadores y decide cuándo pararlos, según recursos y marcador.
5. **Workers** (`workers/`): adaptadores sobre las herramientas de factorización CLI.
6. **Modelo de coste** (`benchmark/predict_cost.py`): estima tiempo/RAM por tamaño.

Por objetivo, el "ancla" se elige por tamaño: **YAFU (SIQS)** por debajo de
95 dígitos y **CADO-NFS (GNFS)** desde ahí (`GNFS_MIN_DIGITS` en `main.py`);
GMP-ECM corre siempre en paralelo. Cuando el controlador redimensiona GNFS,
este reanuda desde el *parameter snapshot* de CADO en vez de empezar de cero.

## Uso

```bash
pip install -r requirements.txt
# Coloca los enteros de reto, uno por línea, en data/target_numbers.txt
python main.py
```

- **Log**: toda la corrida (incluidos los servicios) queda en
  `runtime/primecracker.log` con marca de tiempo.
- **Reanudación**: lo ya entregado se registra en `runtime/solved_history.jsonl`
  y no se vuelve a atacar en la siguiente corrida.
- **Antes de cada competición, vacía `runtime/`**: al arrancar, el submitter
  entrega también los `factor_found_*.json` que hayan quedado de corridas o
  pruebas anteriores.

## Tests

```bash
pytest -q                    # todo (≈7 s)
pytest -q -m "not integration"   # sin los binarios reales de bin/active
```

`test_workers.py` en la raíz es un smoke test manual (lanza los tres
binarios sobre RSA-100); pytest no lo recoge.

## Ámbito y uso aceptable

- Pensado para **tus propios retos**: CTFs en los que participas, ejercicios de curso,
  o números que tú mismo generas para practicar.
- No introduzcas aquí módulos de criptografía de terceros ni datos que no tengas
  autorización explícita para procesar.
