# primecracker

**Orquestador de factorización de enteros para retos CTF de matemática/criptografía.**

Dado un conjunto de enteros compuestos publicados como reto al empezar una competición
*Capture The Flag* (CTF), encuentra sus factores primos en el menor tiempo posible con un
portátil: primero busca debilidades que lo resuelven al instante y después reparte el
tiempo disponible entre las mejores herramientas de factorización (YAFU, CADO-NFS,
GMP-ECM) según el tamaño de cada número.

Es una herramienta **educativa y de competición**. No ataca sistemas, redes ni claves de
terceros: los "objetivos" son **números de reto**, publicados a propósito para ser
factorizados. Las debilidades que explota (primos demasiado cercanos, módulos que
comparten un primo, potencias perfectas, `p−1` suave) son los ejercicios clásicos sobre
*por qué una mala generación de claves es insegura*.

**Contenido**

1. [Inicio rápido](#1-inicio-rápido)
2. [Instalación](#2-instalación)
3. [Uso durante el CTF](#3-uso-durante-el-ctf)
4. [Configuración](#4-configuración)
5. [Cómo funciona](#5-cómo-funciona)
6. [Matemáticas y decisiones de diseño](#6-matemáticas-y-decisiones-de-diseño)
7. [Límites: qué se puede factorizar y qué no](#7-límites-qué-se-puede-factorizar-y-qué-no)
8. [Compatibilidad](#8-compatibilidad)
9. [Fallos posibles y soluciones](#9-fallos-posibles-y-soluciones)
10. [Tests, benchmark y desarrollo](#10-tests-benchmark-y-desarrollo)
11. [Glosario y ámbito de uso](#11-glosario-y-ámbito-de-uso)

---

## 1. Inicio rápido

Con la toolchain ya compilada en `bin/` (ver [2.3](#23-toolchain-gmp-ecm-yafu-y-cado-nfs)):

```bash
pip install -r requirements.txt
# los números del reto, uno por línea:
nano data/target_numbers.txt
python main.py --minutes 120
# resultados para entregar a mano:
cat runtime/para_entregar.txt
```

---

## 2. Instalación

### 2.1 Requisitos

| | Mínimo | Probado |
|---|---|---|
| Sistema | Linux x86_64 | Fedora 43, kernel 7.2 |
| Python | 3.11 (usa `tomllib`) | 3.14.7 |
| Docker | Solo para compilar la toolchain | — |
| CPU | Cualquier x86-64-v3 (AVX2) para la variante `portable` | i7-9750H, 6 núcleos / 12 hilos |
| RAM | 8 GB (YAFU con 100 dígitos llega a ~2 GB) | 15 GB |
| Disco | ~2 GB (toolchain ~800 MB + relaciones de GNFS) | — |

No funciona en macOS ni Windows: ver [8. Compatibilidad](#8-compatibilidad).

### 2.2 Dependencias de Python

```bash
pip install -r requirements.txt          # ejecución
pip install -r requirements-dev.txt      # + pytest
```

Las versiones están fijadas a las probadas. `flask` y `requests` no las importa este
proyecto sino **CADO-NFS**, que se ejecuta con el mismo Python: sin ellas GNFS no
arranca.

### 2.3 Toolchain: GMP-ECM, YAFU y CADO-NFS

Las tres herramientas se compilan en Docker (`build/Dockerfile`, Ubuntu 22.04) sobre
GMP 6.3.0 y se extraen a `bin/`. **Hazlo antes del CTF**: compilar CADO-NFS lleva un
buen rato.

```bash
build/extract.sh                         # variante portable (x86-64-v3) en bin/portable
build/extract.sh --with-native           # + variante native (-march=native) en bin/native
build/extract.sh --with-native --offline # native sin red, desde vendor/
```

- **portable** funciona en cualquier CPU con AVX2. **native** se optimiza para la CPU
  que compila y es algo más rápida; compílala en la máquina con la que vas a competir.
- **offline**: si en el CTF no hay red fiable, prepara antes los mirrors en `vendor/`
  (el propio `extract.sh` te dice los comandos si faltan) y compila sin conexión.
- Las versiones están **fijadas** en el `Dockerfile` (GMP-ECM 7.0.7, YAFU 3.1.9, CADO-NFS
  del 2026-09-24) y el tarball de GMP se verifica por SHA-256. Para actualizarlas: cambia
  los `*_REF`, vuelve a medir el benchmark y pasa los tests de integración.

Al arrancar, `main.py` ejecuta `build/setup_toolchain.sh`, que prueba `native` y luego
`portable` (detecta `SIGILL` si la CPU no soporta las instrucciones) y enlaza la que
funcione en `bin/active`.

### 2.4 Calibración de la máquina

Las estimaciones de coste salen del benchmark de un i7-9750H (6 núcleos / 12 hilos,
2,6 GHz, **turbo desactivado**). En otra máquina, `main.py` mide al arrancar su velocidad
relativa: factoriza con YAFU un número fijo de 70 dígitos (~15 s) y lo compara con la
referencia (6,5 s). El factor resultante (`<1` = más rápida) escala todas las
estimaciones. Se guarda en `config/machine.json` y se repite solo si cambian la CPU, los
hilos o la frecuencia máxima, que es lo que pasa al activar o desactivar el turbo. Para
verlo sin lanzar nada:

```bash
python -m benchmark.machine
```

Si YAFU falla, se usa una estimación por núcleos × GHz (un hilo de SMT cuenta como 0,25
de núcleo). Es mucho menos precisa: no capta la arquitectura, la memoria ni el turbo
real, y puede desviarse un 30–50%.

> **Turbo:** comprueba `cat /sys/devices/system/cpu/intel_pstate/no_turbo` (1 =
> desactivado). Activarlo durante el CTF puede acelerar bastante la factorización, a
> cambio de más temperatura. El controlador para el ancla si la CPU pasa de
> `temp_critical_c`.

### 2.5 Comprobar la instalación

```bash
pytest -q                          # 151 tests en ~10 s, 2 con los binarios reales
python -m scripts.smoke_workers    # arranca, redimensiona y para ECM, YAFU y CADO
```

---

## 3. Uso durante el CTF

1. **Objetivos**: pega los números en `data/target_numbers.txt`, uno por línea (se
   ignoran comentarios `#` y duplicados). `main.py` ataca los que hay **al arrancar**: si
   se publican retos nuevos a mitad, añádelos y **relanza** (lo ya resuelto no se repite).
2. **Tiempo disponible**: `--minutes N` (recomendado). Si no, `time_remaining_seconds`
   de `state.json` (se descuenta lo que haya pasado desde que lo guardaste), o el valor
   por defecto de `config/settings.toml` (60 min).
3. **Marcador** (opcional, pero es lo que permite decidir contra quién compites): copia
   en `state.json` las longitudes que el marcador muestra de cada rival:
   ```json
   {
     "leaderboard": {
       "equipo_a": [120, 95, 90],
       "equipo_b": [{"digits": 110, "minute": 12}, 100]
     }
   }
   ```
   Tus propias longitudes no hacen falta: salen de lo que ya has resuelto. El minuto del
   CTF en que lo rompió (`minute`) es opcional y activa la señal de "en ese grupo hay un
   número débil" ([6.6](#66-qué-atacar-la-estrategia)). Se relee cada vez que se decide
   qué atacar, así que basta con actualizarlo cuando cambie el marcador. Si no cumple
   `config/state_schema.json`, el observador registra un error en el log.
4. **Lanza**:
   ```bash
   python main.py --minutes 120
   python main.py --minutes 120 --log-level WARNING   # consola más callada
   ```
5. **Entrega a mano**: la entrega del CTF es por Outlook con la cuenta de la
   organización y no se puede automatizar. Cada resultado válido aparece en consola como
   `[ENTREGAR A MANO]` y se anota en **`runtime/para_entregar.txt`**:
   ```
   2026-10-23 16:49  N (70 dígitos) = 7864482278502925907283304683680441097458417573046612231298027154307107
       factores: 81661249277100766084485140820967529 * 96306171508794973286930761824051883    [YAFU]
   ```
   Solo se anotan factorizaciones **completas en primos** (verificadas: producto exacto y
   Miller-Rabin) de números que estén en la lista de objetivos, y cada número una sola
   vez.
6. **Si se corta** (Ctrl-C, cuelgue, batería): vuelve a lanzar. Lo ya registrado en
   `runtime/solved_history.jsonl` no se repite.

En el log verás el razonamiento de cada decisión:

```
[estrategia] Tu marcador: (80,); puesto esperado 2.0 de 2.
[estrategia] Para superar a rival (80, 70) necesitas romper al menos un número de 71 dígitos.
[estrategia]   79 dígitos ×1: ~1 min, P(terminar)=1.00, puestos +1.00
[estrategia]   70 dígitos ×3: ~0 min, P(terminar)=1.00, puestos +0.50
```

**Qué hay en `runtime/`** (no versionado):

| Archivo / carpeta | Contenido |
|---|---|
| `para_entregar.txt` | Lo que hay que entregar, legible |
| `solved_history.jsonl` | Registro completo de resultados (para reanudar) |
| `primecracker.log` | Log completo con fecha, nivel y módulo |
| `partial/` | Factorizaciones con algún factor compuesto, a la espera de completarse |
| `ignored/` | Resultados de números que no son objetivos actuales (restos de pruebas) |
| `cado/`, `yafu/` | Directorios de trabajo, se borran al terminar cada objetivo |

**Entre competiciones** conviene vaciar `runtime/` para que el historial no salte
números que coincidan por casualidad.

---

## 4. Configuración

`config/settings.toml`. Todo es opcional; una clave mal escrita hace fallar el arranque
(para que una errata no deje el valor por defecto en silencio).

| Clave | Por defecto | Qué hace |
|---|---|---|
| `strategy` | `longest` | `longest`: gana el número más largo roto ([6.6](#66-qué-atacar-la-estrategia)). `count`: cuantos más números, mejor |
| `toolchain_dir` | `bin/active` | Dónde están `ecm_bin`, `yafu_bin`, `cado-nfs/` |
| `total_threads` | `0` (= núcleos lógicos) | Hilos de la máquina |
| `anchor_threads` | `0` (= todos) | Hilos de YAFU/CADO |
| `gnfs_min_digits` | `93` | Desde aquí CADO-NFS en vez de YAFU (cruce medido, ver [6.4](#64-yafu-o-cado-el-cruce-medido)) |
| `ecm_pretest_b1`, `ecm_pretest_curves`, `ecm_pretest_seconds` | `250000`, `450`, `120` | Pretest ECM antes de GNFS (ver [6.5](#65-por-qué-todos-los-núcleos-al-ancla-y-ecm-solo-como-pretest)); `0` segundos lo desactiva |
| `total_minutes` | `60` | Tiempo total si no hay `--minutes` ni `state.json` |
| `budget_margin` | `1.5` | Holgura sobre el tiempo estimado de cada objetivo |
| `min_target_seconds` | `60` | No se lanza nada con menos tiempo que esto |
| `default_target_seconds` | `180` | Presupuesto si no hay estimación para el método |
| `ram_critical_pct`, `temp_critical_c` | `90`, `85` | Por encima, el controlador para el ancla |
| `ecm_parallel` | `false` | Modo antiguo: ECM junto al ancla todo el ataque, con reparto AIMD |
| `enemy_kill_switch`, `d_safe_bits` | `false`, `330` | Abortar GNFS si el rival (en `state.json`) ha roto más de `d_safe_bits` |

**Tras cambiar de máquina**, repite el benchmark ([10](#10-tests-benchmark-y-desarrollo))
y ajusta `gnfs_min_digits` al cruce que salga: depende de la CPU.

---

## 5. Cómo funciona

```
data/target_numbers.txt
        │
        ▼
 Fase 0: barrido (segundos) ──► resuelto completo en primos ──┐
   GCD cruzado, potencia perfecta,                             │
   trial division, p−1, rho, Fermat                            │
        │ lo que queda                                         │
        ▼                                                      │
 Estrategia (state.json): ¿qué tamaño sube más mi puesto?      │
   utilidad = P(terminar a tiempo) × puestos ganados           │
   grupo grande → caza del débil con ECM antes de GNFS         │
        │      (se vuelve a decidir tras cada número roto)     │
        ▼                                                      │
  < 93 dígitos: YAFU (todos los núcleos)                       │
 ≥ 93 dígitos: pretest ECM ──► CADO-NFS (todos los núcleos)    │
        │       (≤ 2 min)       reanudable entre pasadas       │
        ▼                                                      │
 ¿algún factor compuesto? → se ataca como sub-objetivo         │
        │                                                      ▼
        └────────────────────────────────────►  collector: valida y anota
                                                 runtime/para_entregar.txt
```

En paralelo corren tres servicios: el **monitor de recursos** (CPU, RAM y temperatura
de la CPU), el **observador del marcador** (`state.json`) y el **recolector**. Si uno
cae, se relanza al empezar el siguiente objetivo.

El **controlador** (`orchestrator/controller.py`) revisa cada 5 s y para el ancla si:
- la RAM o la temperatura superan el límite;
- la ETA de la criba de CADO proyecta un final más allá del plazo del objetivo (mejor
  liberar el tiempo para otro número);
- con `enemy_kill_switch`, el rival ha roto más de `d_safe_bits`.

---

## 6. Matemáticas y decisiones de diseño

### 6.1 Notación

Se usa la notación L estándar para algoritmos subexponenciales:

$$L_N[\alpha, c] = \exp\left((c + o(1))\,(\ln N)^{\alpha}\,(\ln \ln N)^{1-\alpha}\right)$$

Con α = 0 es polinomial en ln N y con α = 1 exponencial. Todo lo útil para números
grandes está en medio. **N** es el número a factorizar y **p** su menor factor primo.

### 6.2 Coste de cada método

| Método | Coste | Depende de | Dónde se usa |
|---|---|---|---|
| Trial division (hasta B) | O(B) | p ≤ B | Fase 0 (B = 10⁵) |
| GCD cruzado | O(k²) mcd entre k números | Que dos objetivos compartan un primo | Fase 0 |
| Potencia perfecta | O(log N) raíces enteras | N = pᵏ | Fase 0 |
| Fermat | ≈ (q − p)² / (8√N) iteraciones | p y q muy cercanos | Fase 0 (10⁵ iteraciones) |
| Pollard p−1 | O(B log B) | p − 1 B-suave | Fase 0 (B = 10⁵) |
| Pollard rho | O(√p) esperado | p pequeño | Completar factores en primos |
| ECM | L_p[1/2, √2] | **p**, no N | Pretest antes de GNFS |
| SIQS (YAFU) | L_N[1/2, 1] | N | < 93 dígitos |
| GNFS (CADO-NFS) | L_N[1/3, (64/9)^{1/3} ≈ 1.923] | N | ≥ 93 dígitos |

La distinción clave: **rho y ECM dependen del tamaño del menor factor p** (y p−1, de
que p−1 sea suave), mientras que **SIQS y GNFS dependen solo de N**. Un número de 300 dígitos con un factor de 20 cae
en segundos con ECM; un semiprimo equilibrado de 110 dígitos no le da a ECM ninguna
ventaja.

### 6.3 Fase 0: por qué barrer primero

Los números de CTF suelen tener alguna debilidad de construcción: eso es el reto. Cada
comprobación de la Fase 0 cuesta milisegundos o pocos segundos y, si acierta, ahorra
minutos u horas:

- **GCD cruzado.** Si dos módulos comparten un primo, `mcd(N₁, N₂)` lo revela sin
  factorizar nada (es el fallo de los generadores con poca entropía). Con k objetivos
  son k(k−1)/2 mcd. En un CTF k son decenas, así que no hace falta el *batch GCD* con
  árbol de productos.
- **Fermat.** Empieza en a = ⌈√N⌉ y busca a² − N = b². Con p y q a distancia
  Δ = q − p hacen falta ≈ Δ²/(8√N) iteraciones, así que 10⁵ iteraciones detectan
  Δ ≲ 900·N^{1/4}. Para un N de 100 dígitos son primos de 50 dígitos que coinciden en
  sus ~22 primeros dígitos.
- **p−1 de Pollard.** Encuentra p si todos los factores primos de p−1 son ≤ B. Es una
  debilidad clásica de los primos "a mano".
- **Potencia perfecta.** Se prueban raíces k-ésimas exactas por bisección.

**Solo da por resuelto lo que queda completo en primos.** Un método puede dar `p × c` con
`c` compuesto: trial division sobre 10⁴²+57 da 14923 × (compuesto de 38 dígitos).
`split_to_primes` intenta romperlo con rho durante unos segundos; si no puede, el número
pasa a los workers.

### 6.4 YAFU o CADO: el cruce medido

Asintóticamente GNFS (L[1/3]) gana a SIQS (L[1/2]), pero sus constantes son mucho
peores: en la práctica el cruce se suele citar en torno a 95-100 dígitos, y depende de
la implementación y de la máquina. En este equipo, con
12 hilos:

| Dígitos | YAFU (SIQS) | CADO-NFS (GNFS) |
|---|---|---|
| 80 | 35 s | 109 s |
| 85 | 92 s | 176 s |
| 90 | 253 s | 310 s |
| 95 | 628 s | **448 s** |
| 100 | 1793 s | **1002 s** |

El **cruce está en 93 dígitos** (`benchmark.predict_cost.crossover_digits()`), y ese es
el valor de `gnfs_min_digits`.

**Modelo de coste.** Cada método se ajusta a

$$T(d) = t_0 + \exp\left(a + c \cdot (\ln N)^{\alpha} (\ln\ln N)^{1-\alpha}\right)$$

con α = 1/2 para SIQS y 1/3 para GNFS. El término **t₀ es el coste fijo de arranque**:
~1.6 s en YAFU y ~26 s en CADO, que levanta un servidor y sus clientes. Sin él, los
números pequeños (casi todo arranque) aplanaban la curva y el modelo estimaba 34 s para
YAFU con 90 dígitos cuando tardaba 253 s. El ajuste actual se desvía como mucho un ~30%
en el rango medido (YAFU a 80 dígitos); `budget_margin = 1.5` lo cubre.

**Teoría frente a medida.** Por cada 10 dígitos más, L[1/3, 1.923] crece ×5.6 (de 90 a
100) y L[1/2, 1] ×8.5. Lo medido es ×3.2 para CADO y ×7.1 para YAFU. GNFS crece aún por
debajo de su asíntota porque a este tamaño pesan los costes fijos, el término o(1) y el
álgebra lineal. Por eso **las extrapolaciones más allá de 100 dígitos son inciertas**, y
en [7](#7-límites-qué-se-puede-factorizar-y-qué-no) se dan como rango.

### 6.5 Por qué todos los núcleos al ancla, y ECM solo como pretest

El coste de ECM depende del menor factor p. Para encontrar un factor de 50 dígitos, la
tabla de GMP-ECM pide ~8266 curvas con B1 = 43·10⁶: horas o días en un portátil. En un
**semiprimo equilibrado** de 100 dígitos (p ≈ 10⁵⁰), la probabilidad de que ECM acierte
dentro de los minutos que tarda GNFS es despreciable. Correr ECM en paralelo, como
hacía la versión anterior, solo le quitaba la mitad de los núcleos a GNFS y **lo hacía
el doble de lento**.

Pero un número de CTF de 100 dígitos puede ser `p₂₀ × q₄₀ × r₄₀`. Por eso, antes de GNFS,
hay un **pretest acotado**: 450 curvas con B1 = 250 000, que es el número esperado de
curvas para un factor de 30 dígitos. Da ~63% (1 − 1/e) de acertar uno de 30 dígitos y
casi seguro uno de 20 o 25. Medido sobre 100 dígitos con 12 hilos: **60 s** para las 450
curvas (tope: 2 min, y nunca más del 25% del presupuesto del objetivo), frente a los
~17–19 min de GNFS. Si encuentra un
factor, el cofactor se ataca como sub-objetivo (normalmente con YAFU, que es más
pequeño). En la prueba de extremo a extremo, `p₂₀ × q₄₀ × r₄₀` (100 dígitos) cayó así en
~45 s: ECM dio el p₂₀ y YAFU partió el cofactor de 80 dígitos.

Por debajo de 93 dígitos no hay pretest propio: `factor()` de YAFU ya hace el suyo
(rho, p−1 y ECM escalonado) antes de SIQS.

### 6.6 Qué atacar: la estrategia

**Las reglas del CTF.** Gana quien rompe el número más largo; si hay empate, el
siguiente más largo, y así sucesivamente; con empate en todo, quien llegó antes. Así que
cada equipo es un **vector de longitudes rotas ordenado de mayor a menor**, comparado
**lexicográficamente**:

$$(120, 95) \succ (120, 90) \succ (120) \succ (110, 105, 100)$$

Diez números de 80 dígitos no valen lo que uno de 110. Por eso **ordenar de menor a
mayor**, que maximiza *cuántos* números rompes, **es la estrategia equivocada** aquí.

**La función de utilidad** (`orchestrator/strategy.py`). Para cada tamaño `d` que queda
por romper:

$$U(d) = P(T_d \le t_{\text{restante}}) \cdot \big(\text{puesto actual} - \text{puesto si rompes } d\big)$$

- **Puesto.** 1 más el número de rivales cuyo vector supera al tuyo. Un empate exacto
  cuenta como medio puesto, porque lo decide el tiempo y no sabemos quién llegó antes. El
  marcador solo enseña las 2–3 mayores longitudes de cada rival: si coincides en ellas,
  lo de detrás no se ve y también cuenta como empate.
- **Probabilidad de terminar.** El tiempo real se modela como log-normal alrededor de la
  estimación del modelo de coste, con σ = 0,3 en ln T (el error medido es de hasta ~30%):

  $$P(T_d \le t) = \Phi\left(\frac{\ln t - \ln \hat{T}_d}{0.3}\right)$$

- **Desempate:** la mejora esperada del propio vector, P · (valor lexicográfico nuevo −
  actual). Si ningún tamaño cambia el puesto (vas primero, o te faltan dos números para
  superar a alguien), sigues mejorando tu vector, que es el desempate futuro.

De ahí salen solas las conductas que importan:
- **"No se gana con menos de X".** Un tamaño que no te hace adelantar a nadie tiene
  utilidad de puesto 0, y el log dice explícitamente cuál es la longitud mínima que
  necesitas contra cada rival.
- **Si vas por detrás, arriesga.** Un número grande con P = 0,3 que te da el primer
  puesto vale más que uno seguro que no te mueve.
- **Si vas por delante, asegura.** Lo que suma desempate con alta probabilidad.

**Caza del débil.** Con ~10 números de cada tamaño, dentro de un grupo todos puntúan
igual: interesa el **más débil**, no uno cualquiera. Antes de dedicar GNFS a un grupo se
pasa el pretest ECM (~1–2 min) por todos sus números si eso cuesta menos de 1/3 de lo
que costaría GNFS, o siempre que el marcador lo sugiera: si un rival rompió un tamaño en
menos del 10% de lo que costaría con GNFS en esta máquina, es casi seguro que encontró
uno débil. El margen ×10 cubre que el rival tenga más hardware.

**Replanificación.** Tras cada número roto se relee `state.json` y se vuelve a decidir.
Si GNFS se aborta porque su ETA no llega al final, ese tamaño se descarta (los demás del
grupo son igual de duros) y se elige otro.

**Límite conocido.** Se mira un paso por delante. Si te faltan dos números para superar a
alguien, el primero no mejora el puesto por sí solo y decide el desempate. En la
práctica eso lleva igualmente al tamaño mayor alcanzable.

**Estrategia `count`** (para CTF que puntúan por cantidad): de menor a mayor coste.
Con un plazo común, ordenar por menor tiempo de proceso primero (regla SPT)
**maximiza el número de trabajos terminados**. Cada objetivo recibe coste estimado ×
1,5, lo que no cabe se pospone y una segunda pasada lo reintenta con el tiempo restante.

**En las dos estrategias:**
- GNFS **reanuda su criba** desde el *parameter snapshot* de CADO cuando se vuelve a un
  número, y los factores parciales encontrados no se pierden.
- **Aborto por proyección**: CADO publica la ETA de la criba. El final total se estima
  como ETA + 35% de la duración de la criba (filtrado, álgebra lineal y raíz cuadrada).
  Si ese final cae fuera del plazo, se para y el tiempo pasa a otro objetivo.

### 6.7 Verificación de resultados

- **Producto exacto** y todos los factores > 1. Así se rechaza `N × 1`, que ECM da
  cuando todas sus curvas encuentran N a la vez.
- **Primalidad: Miller-Rabin.** Es determinista para n < 3.3·10²⁴ con los 12 primeros
  primos como testigos. Por encima usa 40 testigos aleatorios: la probabilidad de dar por
  primo un compuesto es < 4⁻⁴⁰ ≈ 10⁻²⁴.
- Solo se anota lo que es **objetivo actual** y **no se había anotado antes**.

### 6.8 Decisiones técnicas que no son matemáticas

| Decisión | Motivo |
|---|---|
| Duraciones con `time.monotonic()`, plazos del CTF con reloj de pared | Las duraciones no deben contar una suspensión del portátil (en una medición, 12 h de suspensión contaron como 12 h de cómputo); el reloj del CTF sí sigue corriendo |
| Workdirs de CADO en `runtime/cado/`, no en `/tmp` | En muchas distribuciones `/tmp` es tmpfs (RAM): las relaciones de GNFS ocupan GB |
| Cada worker lee siempre la salida de su proceso | Un pipe sin leer se llena (64 KB) y el proceso se bloquea: YAFU se quedaba colgado |
| YAFU recibe `factor(N)` por stdin | Sin terminal, YAFU 3.x lee stdin como archivo de comandos e ignora el argumento |
| `siqs.dat` se borra al reiniciar YAFU | El que deja un proceso matado queda truncado y YAFU se cuelga al leerlo |
| `kill()` mata el grupo de procesos y los clientes demonizados de CADO | Si no, la criba seguía en segundo plano robando CPU |
| Escrituras atómicas (`.tmp` + `os.replace`) | El recolector nunca lee un archivo a medias |
| Se espera a que el monitor del worker termine (`busy()`), no solo el proceso | Tras un factor, el worker mata el proceso y descompone en primos hasta 5 s antes de avisar: mirar solo el proceso daba el objetivo por fallido en ese hueco |
| Al apagar se registra lo pendiente en `runtime/` | Un resultado escrito justo antes de salir llegaba con el recolector ya parado y no aparecía en `para_entregar.txt` |
| El recolector no envía nada | La entrega es manual (Outlook con cuenta de la organización) |

---

## 7. Límites: qué se puede factorizar y qué no

Estimaciones para este equipo (i7-9750H, 12 hilos, turbo desactivado) en el caso
difícil, un semiprimo equilibrado. En otra máquina, multiplica por su factor de
calibración ([2.4](#24-calibración-de-la-máquina)). La cota baja sale del modelo ajustado y la alta de aplicar la tasa teórica
de GNFS (×5 por cada 10 dígitos) desde lo medido a 100.

| Dígitos (bits) | Método | Tiempo estimado | ¿Viable en un CTF? |
|---|---|---|---|
| ≤ 70 (232) | YAFU | segundos | Sí |
| 80–90 (266–299) | YAFU | 0.5–4 min | Sí |
| 100 (332) | CADO | ~17 min (benchmark), ~19 min dentro de `main.py` (medido) | Sí |
| 110 (365) | CADO | 45 min – 1.5 h | Sí, si es casi lo único |
| 120 (399) | CADO | 2 – 7 h | Solo en CTF largos |
| 130 (432) | CADO | 5 – 35 h | No, salvo debilidad |
| 155 (512, RSA-512) | CADO | días–semanas en un portátil | No |
| 232 / 250 (RSA-768 / RSA-250) | — | ~2000 / ~2700 años-núcleo (récords académicos) | No |
| 617 (RSA-2048) | — | inviable con la tecnología actual | No |

La RAM también limita: YAFU con 100 dígitos ya usó ~2 GB y CADO ~600 MB. El álgebra
lineal de GNFS crece con el tamaño.

**Implicación práctica**: por encima de ~115 dígitos, un número de CTF solo es resoluble
en horas si tiene una debilidad. La Fase 0 y el pretest ECM están para eso, y es donde
merece la pena mirar a mano (¿primos cercanos?, ¿módulos relacionados?) antes de lanzar
GNFS.

---

## 8. Compatibilidad

| | Estado |
|---|---|
| **Linux x86_64** | Soportado (probado en Fedora 43) |
| Linux ARM64 | No probado. La toolchain se compila con flags x86 (`-march=x86-64-v3`); habría que adaptar el `Dockerfile` |
| macOS | No: la toolchain se compila en un contenedor Linux (binarios ELF) y el worker de ECM usa `stdbuf` (GNU coreutils). Habría que compilar GMP-ECM, YAFU y CADO-NFS de forma nativa |
| Windows | No: además no hay grupos de procesos POSIX (`os.killpg`). WSL2 debería funcionar como Linux, sin probar |
| Python < 3.11 | No (`tomllib`, sintaxis `X \| None`) |
| CPU sin AVX2 | La variante `portable` no arranca (`SIGILL`). Compila `native` en esa máquina |
| Temperatura | Sensores de CPU `coretemp` (Intel), `k10temp`/`zenpower` (AMD), `cpu_thermal` (ARM), `acpitz` como último recurso. Sin ninguno, la regla de temperatura se desactiva con un aviso |

---

## 9. Fallos posibles y soluciones

| Síntoma | Causa probable | Solución |
|---|---|---|
| `Toolchain no verificada. Abortando` | `bin/` vacío o ninguna variante compatible | `build/extract.sh --with-native` en esta máquina |
| `SIGILL, instrucción no soportada` en el pre-flight | Binarios compilados para otra CPU | Usa `portable` o recompila `native` aquí |
| CADO falla al arrancar: `No module named flask` / `requests` | Falta su dependencia de Python | `pip install -r requirements.txt` |
| CADO: `cannot open shared object file` (`libhwloc`…) | Falta `bin/active/cado-nfs/lib` | Recompila con `extract.sh`, que empaqueta esas librerías |
| CADO: no encuentra su carpeta de build | Distinto *hostname* que el contenedor de compilación | El worker crea el enlace solo; si no tiene permisos, revisa `bin/active/cado-nfs/build/` |
| CADO termina en ~1 s con números pequeños | CADO no tiene parámetros por debajo de ~60 dígitos | Normal: por debajo de 93 dígitos se usa YAFU |
| `could not open yafu.ini` | YAFU busca su configuración en el directorio de trabajo | Inofensivo: funciona con los valores por defecto |
| `[!] No se encontró sensor de temperatura de CPU` | Sin `coretemp`/`k10temp`… (VM, contenedor) | Inofensivo: la regla de temperatura queda desactivada |
| `config/settings.toml: claves desconocidas` | Errata en una clave | Corrige el nombre (el error lista las válidas) |
| Un objetivo grande se "pospone" y no se ataca | Su coste estimado no cabe en el tiempo restante | Más `--minutes`, o atácalo solo; en la 2.ª pasada recibe todo lo que quede |
| GNFS se aborta con "proyección… no converge" | La ETA de la criba no llega al plazo | Es intencionado. Con más tiempo, la 2.ª pasada lo reanuda desde la criba |
| Un número no aparece en `para_entregar.txt` | Factorización incompleta, no es objetivo, o ya anotado | Mira `runtime/partial/`, `runtime/ignored/` y `primecracker.log` |
| Un reto publicado a mitad no se ataca | `main.py` lee los objetivos al arrancar | Añádelo a `data/target_numbers.txt` y relanza |
| `Error de validación/lectura` cada 2 s en el log | `state.json` no cumple su schema (p.ej. longitudes entre comillas) | Corrígelo según [3](#3-uso-durante-el-ctf), o bórralo y usa `--minutes` |
| `Factor de velocidad (núcleos × GHz)` en vez de `(calibración)` | La calibración con YAFU falló | Revisa la toolchain y borra `config/machine.json` para recalibrar |
| Las estimaciones no cuadran tras activar el turbo | Calibración de antes del cambio | Se recalibra sola si cambia la frecuencia máxima; si no, borra `config/machine.json` |
| La estrategia no ataca el número que esperabas | Ese tamaño no mejora tu puesto, o no se termina a tiempo | Mira las líneas `[estrategia]` del log: dicen la probabilidad y los puestos de cada tamaño |
| RAM al límite / el sistema va lento | YAFU o CADO grandes; GNFS guarda relaciones | El controlador para el ancla al 90%; cierra otros programas |
| El portátil se suspende | Ahorro de energía | Desactiva la suspensión durante el CTF: con la tapa cerrada se para todo |
| Tiempos del benchmark absurdamente altos | Suspensión durante la medición con una versión antigua | Las mediciones actuales usan reloj monótono; repite el punto afectado |

Para diagnosticar, el primer sitio es `runtime/primecracker.log`: incluye la salida de
los servicios y las excepciones no controladas de cualquier hilo.

---

## 10. Tests, benchmark y desarrollo

```bash
pytest -q                          # todo
pytest -q -m "not integration"     # sin binarios reales (lo que corre en CI)
python -m scripts.smoke_workers    # prueba manual de los tres binarios
```

La CI (`.github/workflows/tests.yml`) ejecuta los tests sin integración en cada push.

**Benchmark** (repetir al cambiar de máquina o de versión de la toolchain):

```bash
python -m benchmark.run_benchmark --start 60 --stop 100 --step 5 \
    --methods YAFU CADO-NFS --threads 12 --timeout 3600
python -c "import benchmark.predict_cost as p; print(p.crossover_digits())"
```

Añade filas a `benchmark_results.csv`. Mide con todos los núcleos, sin contar
suspensiones y sin dejar restos en `runtime/`. Tarda ~1.5 h para 60–100 dígitos.

**Estructura**:

| Ruta | Qué hay |
|---|---|
| `main.py` | Punto de entrada: Fase 0, reparto del tiempo, pasadas, sub-objetivos |
| `screening/vulnerability_sweep.py` | Fase 0 |
| `workers/` | Adaptadores de ECM, YAFU y CADO; `quick_factor.py`: métodos baratos y primalidad |
| `orchestrator/` | Controlador, estrategia (`strategy.py`), monitor de recursos, observador del marcador |
| `submitter/collector.py` | Validación y registro para la entrega manual |
| `benchmark/` | Medición, modelo de coste y calibración de la máquina (`machine.py`) |
| `settings.py`, `config/` | Configuración y schema de `state.json` |
| `build/` | Compilación de la toolchain |
| `tests/` | Tests (pytest) |

---

## 11. Glosario y ámbito de uso

El código usa vocabulario de competición. Significa esto y solo esto:

| Término | Significado |
|---|---|
| `target` / objetivo | Un entero de reto de `data/target_numbers.txt` |
| ancla | El factorizador principal de un objetivo: YAFU o CADO-NFS |
| `enemy` / rival | El otro equipo **en el marcador del CTF**. Nunca una máquina ni una persona |
| `scoreboard` | El marcador de la competición (`state.json`, editado a mano) |
| `vulnerability_sweep` | Barrido de **debilidades matemáticas** del propio número de reto |
| `attack` / atacar | Lanzar los factorizadores contra un número de reto |
| `collector` | Valida y anota los resultados para entregarlos **a mano**; no envía nada |

**Ámbito**: pensado para **tus propios retos**, es decir, CTF en los que participas,
ejercicios de curso o números que generas para practicar. No introduzcas módulos de
criptografía de terceros ni datos que no tengas autorización explícita para procesar.
