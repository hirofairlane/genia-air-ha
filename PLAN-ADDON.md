# PLAN — Genia Air como HA addon autónomo

> **Estado**: 2026-06-13, pivote acordado con Sergio + decisiones autónomas
> confirmadas. **No implementar sin OK explícito.**
> El código en `custom_components/genia_air/` queda como **referencia**
> (parser ebusd, mapeos case-sensitive, catalog de entidades) — el entregable
> final es el addon en el **mismo repo** `github.com/hirofairlane/genia-air-ha`.

## Decisión y motivación

Sergio quiere distribuir una **app autónoma** que controla la Vaillant Genia Air,
no una integración HACS que extrae datos al ecosistema HA. Si su hermana la
instala, debe ser una unidad que:

- Se instala desde un repository de addons HA con un click.
- Lleva todas sus dependencias dentro del Docker (paho-mqtt, flask, apscheduler,
  pandas si hace falta, sqlite).
- Depende sólo de **LukasGrebe ebusd-addon** publicando MQTT en su prefijo
  (`ebusd/+/+`).
- Expone su propia UI con su propio look y sus propias gráficas — no toca el
  Lovelace ni necesita YAMLs de configuración manual del usuario.
- **Opera** la máquina: setpoints, modos op, optimización activa de impulsión y
  ΔT objetivo según condiciones.

Stack de referencia: `ha-energy-optimizer-main` (Flask + PANEL HTML embebido +
Chart.js + APScheduler + JSONL/SQLite).

## Arquitectura — todo dentro del repo actual

El repo `github.com/hirofairlane/genia-air-ha` se reorganiza en topología
mixta donde **addon** (entregable) y **referencia** (integración HACS
descartada) conviven. Layout final:

```
genia-air-ha/                           (repo único — el actual)
├── README.md                           orientado al ADDON: cómo añadirlo
│                                       como HA addon repository, screenshots,
│                                       dependencia ebusd-addon, configuración.
├── repository.yaml                     entrada para HA add-on stores
├── PLAN-ADDON.md                       este documento
├── LICENSE
├── genia_air/                          ★ EL ADDON — slug del addon
│   ├── config.yaml                     manifest HA addon
│   ├── build.yaml                      multi-arch base images
│   ├── Dockerfile
│   ├── README.md                       texto que ve el usuario en la addon store
│   ├── CHANGELOG.md
│   ├── icon.png + logo.png             assets de la store
│   ├── translations/                   en.yaml, es.yaml (config schema labels)
│   └── rootfs/
│       └── usr/
│           ├── bin/
│           │   ├── run.sh              entrypoint
│           │   └── genia_air.py        toda la lógica (Flask + scheduler +
│           │                           PANEL embebido — modelo Energy Optimizer)
│           └── share/
│               └── genia_air/
│                   └── static/         (opcional: si decidimos extraer JS
│                                        del PANEL string para limpieza)
├── docs/
│   ├── ARCHITECTURE.md
│   ├── OPTIMIZER-RULES.md              reglas del control activo
│   └── EBUSD-FIELD-CATALOG.md          mapeo CSV → fields (extraído del
│                                       trabajo de la integración HACS)
└── _reference/                         ★ NO se distribuye con el addon
    └── custom_components_genia_air/    movido aquí desde
                                        `custom_components/genia_air/`.
                                        Sólo referencia del parser.
```

**Migración del repo actual** (a hacer en el primer commit del nuevo
trabajo):
1. `git mv custom_components/ _reference/custom_components_genia_air_legacy/`
2. Crear `genia_air/` con config.yaml + Dockerfile + rootfs/ vacíos pero
   válidos (M0 hello-world).
3. Crear `repository.yaml` en raíz.
4. Sustituir README.md por uno orientado al addon (deprecar mención HACS).
5. Tag `v0.2.0-addon-scaffold` para marcar el corte arquitectural.

### `config.yaml` (addon manifest)

Campos clave:
- `name: "Vaillant Genia Air"`
- `slug: genia_air`
- `version: 0.1.0`
- `arch: [aarch64, amd64, armv7]`
- `startup: services` (debe arrancar después de MQTT y ebusd)
- `boot: auto`
- `init: false`
- `ingress: true` → UI accesible sin puerto expuesto
- `panel_icon: mdi:heat-pump`
- `panel_title: Genia Air`
- `homeassistant_api: true` → leer estados HA (sensores externos: outdoor,
  precio luz, presencia)
- `services: ["mqtt:need"]` → declarar dependencia de MQTT
- `options`:
  - `topic_prefix: ebusd` (configurable)
  - `zone_count: 1` (1-3)
  - `optimize_flow_temp: true`
  - `target_delta_t: 5.0` (K, válido en heating)
  - `min_flow_temp_safe: 14.0` (anti-condensación cooling)
  - `max_flow_temp_safe: 40.0` (suelo radiante)
  - `summer_temp_limit: 19.0`
  - `log_level: info`
- `schema`: validación tipos de cada option

### `Dockerfile`

```dockerfile
ARG BUILD_FROM
FROM $BUILD_FROM

RUN apk add --no-cache python3 py3-pip
RUN pip install --no-cache-dir \
    flask==3.0.* \
    paho-mqtt==1.6.* \
    apscheduler==3.10.* \
    requests==2.31.* \
    pandas==2.1.*

COPY rootfs /
RUN chmod +x /usr/bin/run.sh

CMD ["/usr/bin/run.sh"]
```

### `genia_air.py` — esqueleto de módulos

Estructura interna del único archivo (siguiendo el patrón Energy Optimizer):

```
genia_air.py
├── CONFIG / constants            opciones cargadas de /data/options.json
├── MQTT client                   paho.mqtt.client conectado al broker HA
│   ├── on_connect → subscribe ebusd/+/+
│   ├── on_message → parse payload, push a STATE dict
│   ├── publish_write(circuit, msg, value)   → ebusd/<c>/<m>/set
│   └── request_read(circuit, msg)            → ebusd/<c>/<m>/get
├── STATE                         dict (circuit, msg) → última lectura + ts
├── HISTORY                       SQLite local en /data/history.db
│   ├── tables: snapshots, decisions, errors
│   └── thread-safe writers
├── HA_API                        para leer entidades HA externas (outdoor,
│                                 precio luz desde Energy Optimizer si está)
├── OPTIMIZER                     reglas de control activo
│   ├── compute_target_flow(outdoor, indoor_setpoint, mode) → curve
│   ├── compute_delta_t_action(delta_actual, delta_target) → adjust pump %
│   ├── enforce_safety_limits(value, min, max) 
│   └── apply_decisions() → writes a ebusd vía MQTT
├── SCHEDULER (APScheduler)
│   ├── initial_sync         arranque + cada N min force-read CTLS2
│   ├── snapshot_history     cada 1 min commit a SQLite
│   ├── optimize_cycle       cada 5 min evalúa y escribe setpoints
│   └── health_check         cada 10 min, escribe estado a HA persistent_notif
│                            si hay fault
├── MQTT DISCOVERY            publica device "Genia Air (addon)" con un puñado
│                             de sensores mínimos (state, room_temp, mode)
│                             para que HA tenga 1 device representativo y
│                             automations puedan engancharse
├── REST API (Flask)
│   ├── GET  /api/state              snapshot STATE actual
│   ├── GET  /api/history?series=X&hours=N
│   ├── GET  /api/decisions?limit=N
│   ├── GET  /api/health             OK/WARN/FAIL + razones
│   ├── POST /api/write              {circuit, msg, value} con auth
│   ├── POST /api/mode               {mode: heat|cool|off|auto}
│   ├── POST /api/setpoint           {target_c: float}
│   ├── POST /api/optimize           {enable: bool, target_delta_t: float}
│   └── GET  /                       devuelve PANEL HTML con X-Ingress-Path
└── PANEL = r"""..."""          ~700 líneas HTML+CSS+JS, look Energy Optimizer
    ├── tema oscuro idéntico (--bg:#0f172a, etc.)
    ├── tabs: Estado / Gráficas / Controles / Optimización / Diagnóstico
    ├── KPI cards grandes, batt-card adaptada a thermostat-card
    └── Chart.js con series desde /api/history
```

## UI — pestañas

| Tab | Contenido |
|---|---|
| **Estado** | KPI cards (ΔT, Modulación %, P. eléctrica, P. térmica, COP). Card termostato Z1 (modo + setpoint + ambient). Card sistema (compresor state, caudal, exterior). Estado optimizer (activo/inactivo + última decisión). |
| **Gráficas** | COP 24 h. ΔT 24 h. Energía térmica 7 días. Consumo vs producción 24 h. Horas funcionamiento por modo (semanal). |
| **Controles** | Modo HVAC (off/heat/cool/auto), presets (manual/day/night/holiday). Sliders setpoints (manual/día/noche/holiday/cooling). Sliders config (max/min flow, summer limit). |
| **Optimización** | Toggle optimizer on/off. Target ΔT (K). Estrategia (weather-compensated / fixed / adaptive). Histórico decisiones del optimizer con razón ("flow_target ajustado de 32 a 30 °C: outdoor +12 °C"). Modo "explica" con preview de la próxima decisión. |
| **Diagnóstico** | Tabla de todos los mensajes ebusd vistos. Last seen por circuit. Botón force-read all. Error history HMU. Versión addon + commit + estado MQTT. Logs últimas 100 líneas. |

## Optimización — alcance v0.1

No-go en v0.1 (postpuesto a v0.2):
- ML / scikit. Reglas determinísticas primero.
- Multi-zona (Z2/Z3).
- Integración con precio luz de Energy Optimizer (cooling preventivo en valley).

Go en v0.1:
- **Compensación curva calefacción** según outdoor: si el sistema usa curva
  fija, el optimizer ofrece sobrescribir `Hc1MaxFlowTempDesired` dinámicamente
  dentro del rango seguro `[14, 40]` °C en función de un par
  (outdoor, indoor_setpoint).
- **ΔT objetivo**: si ΔT(supply-return) está consistentemente fuera de
  `target ± 0.8 K` durante ≥ 15 min, log + alerta. (Ajustar caudal de bomba
  no es expuesto por ebusd en el modelo de Sergio — pendiente investigar.)
- **Anti-cycling cooling**: si compressor on/off > N veces/hora en cooling,
  subir setpoint cooling 0.5 °C automáticamente y log.
- **Summer/winter switchover**: si outdoor avg > `summer_temp_limit`, mover a
  cooling auto; si < `summer_temp_limit - 3`, volver a heat auto. Tiempo
  mínimo en cada modo: 24 h (anti-flap).
- **Safety enforcement**: cualquier escritura que el usuario haga desde otro
  cliente HA y caiga fuera de límites seguros (max flow > 40 en suelo
  radiante, min flow < 14 en cooling) se corrige automáticamente. Notifica en
  HA persistent_notification.

## Persistencia interna

`/data/` es el volumen persistente del addon (sobrevive a updates):

```
/data/
├── options.json              gestionado por supervisor (read-only para nosotros)
├── history.db                SQLite — snapshots por minuto últimas 30 días
├── decisions.jsonl           append-only log de decisiones del optimizer
├── setup.json                estado persistente que NO está en options
│                             (calibraciones, overrides manuales temporales)
└── logs/
    └── genia_air.log         rolling, 5 MB × 3
```

## Integración con HA core (mínima pero útil)

Vía MQTT Discovery (el addon publica sus propios topics `homeassistant/.../config`):

- Device "Vaillant Genia Air (addon)" único.
- 6 entidades suficientes para automations externas:
  - `sensor.genia_air_addon_state` (string: idle/heating/cooling/fault)
  - `sensor.genia_air_addon_cop`
  - `sensor.genia_air_addon_delta_t`
  - `binary_sensor.genia_air_addon_fault`
  - `climate.genia_air_addon_zone1` (set_temperature, set_hvac_mode)
  - `switch.genia_air_addon_optimizer` (toggle on/off)

Resto de telemetría / setpoints granulares → sólo en la UI del addon. Sergio
no quería 35 entidades sueltas en HA, y la regla es: HA ve lo necesario para
automatizar, el addon ve todo.

## Migración desde la integración HACS actual

Cuando se publique el addon v0.1:

1. Usuarios que ya tengan la integración `genia-air-ha`: deprecation notice
   en la README. La integración no se desinstala automáticamente; convivencia
   posible pero **dejará entidades duplicadas** (la integración crea
   `sensor.genia_air_*` y el addon vía discovery crea
   `sensor.genia_air_addon_*`).
2. Script de cleanup opcional (Python REST a HA): borrar las 35 entidades
   `sensor.genia_air_*` legacy de la integración, eliminar config entry,
   eliminar `custom_components/genia_air/`.
3. El código de migración legacy (claim de `ebusd_*` unique_ids) **no aplica
   al addon** porque las entidades nuevas son del addon, no de la
   integración. Si el usuario quería preservar histórico de
   `sensor.ebusd_*`, debe haber instalado la integración HACS antes. Para
   nuevos usuarios: history empieza limpia en el addon.

## Configuración de Sergio para v0.1

Hardcoded en defaults del addon (luego override en options):
- Sistema: 1 zona, suelo radiante todas plantas, sin ACS (Magna Aqua aparte).
- Curva: 0.6 (calibrado tras baseline 2026-05-07).
- Max flow: 35 °C (suelo radiante).
- Min flow cooling: 14 °C.
- Summer temp limit: 19 °C.
- Target ΔT heating: 5 K.

## Hitos / iteraciones

| Hito | Alcance | Estimado |
|---|---|---|
| **M0 — Scaffold** | repo nuevo, config.yaml, Dockerfile, run.sh, genia_air.py "hello world" Flask, panel placeholder. Instalable en HA local de Sergio. | 2 h |
| **M1 — MQTT pipeline** | conexión MQTT al broker HA, subscribe ebusd, STATE dict, /api/state devolviendo snapshot. Sin UI todavía más allá de un JSON dump. | 2 h |
| **M2 — UI mínima** | PANEL HTML clonando look Energy Optimizer, tab Estado funcional con KPI cards leyendo /api/state, refresh 5 s. | 3 h |
| **M3 — Controles** | Tab Controles (sliders + selects). POST /api/write probado con setpoint. Toast notifications. | 2 h |
| **M4 — Persistencia + Gráficas** | SQLite snapshots cada 1 min, GET /api/history, tab Gráficas con Chart.js (COP, ΔT, energía 7 días). | 3 h |
| **M5 — Optimizer v0.1** | reglas determinísticas (curva dinámica, anti-cycling, summer/winter, safety enforce). Tab Optimización con explain. | 4 h |
| **M6 — MQTT Discovery** | publica las 6 entidades mínimas hacia HA. | 1 h |
| **M7 — Diagnóstico + polish** | tab Diagnóstico, logs, health endpoint, error history. | 2 h |
| **M8 — README + distribución** | docs usuario, screenshots, repository.yaml, release tag, instrucciones para añadir el repo a HA. | 2 h |

**Total ≈ 21 h** repartibles en sesiones cortas.

## Decisiones autónomas (Sergio: "tira tu solo")

### 1. Nombre del repo / paquete — RESUELTO

Mismo repo actual: `github.com/hirofairlane/genia-air-ha`. Slug del addon:
`genia_air`. No fragmentamos en repos separados.

### 2. Integración HACS — RESUELTO (forget about it)

La integración HACS muere. El directorio `custom_components/` se mueve a
`_reference/` como histórico y se elimina del paquete distribuible. README
nuevo no menciona HACS. Los 2 commits pendientes (`2ab7784` climate+select,
`4ce82ec` initial-sync) se pushean igualmente para preservar el historial
git limpio, después se hace el "great move" en un commit con título claro.

### 3. Auth en POST endpoints — RESUELTO

**Threat model**: el addon corre en la red local de Sergio (192.168.0.0/23).
HA ingress sólo es accesible para usuarios HA autenticados. El riesgo
cross-origin (que un script malicioso en otra pestaña del navegador haga
fetch al endpoint de escritura) es real pero acotado a usuarios ya dentro
de HA.

**Implementación v0.1** — defensa en 2 capas:

1. **Layer ingress** (todas las rutas): el handler comprueba presencia de
   `X-Ingress-Path` header. Si falta, devuelve 403. HA supervisor inyecta
   este header solo cuando la request entra por el túnel ingress (que ya
   autentica al usuario). Esto bloquea peticiones cross-origin desde otros
   addons o web externa.

2. **Layer write-side** (POST/PUT/DELETE): además del header anterior,
   requiere `X-Hass-User` (UUID del usuario HA, también inyectado por
   supervisor). Se loggea en `decisions.jsonl` quién hizo qué cambio.

**No** usamos bearer tokens ni session cookies porque el ingress de HA ya
proporciona el contexto de auth — replicarlo es complejidad innecesaria.

Documentado en `docs/SECURITY.md` cuando se cree.

### 4. Caudal de bomba para control real de ΔT — RESUELTO

Confirmado: en HMU 0901 + CTLS2 0509 + VWZIO 76 de Sergio, ebusd **no
expone control directo del PWM de la bomba**. El HMU lo gestiona
internamente para mantener el equilibrio hidráulico.

**Estrategia v0.1**: el optimizer **no actúa sobre el caudal**. Influye
indirectamente vía:
- `Hc1MaxFlowTempDesired` dinámico (cambia el delta forzando al sistema a
  reajustar internamente).
- `z1ManualTemp` / `z1CoolingTemp` (cambia el setpoint, indirectamente
  cambia la modulación del compresor y por tanto el ΔT).

Si ΔT(supply-return) sale del rango `target ± 0.8 K` durante ≥ 15 min en
funcionamiento estable, sólo **alerta** (HA persistent_notification +
entry en `/api/health`). No intenta corrección activa porque no tenemos la
palanca.

**v0.2 / investigación pendiente**: ver si algún msg de `bai` (system-type
"bai") expone `PumpModul` o equivalente. Si Sergio no lo tiene
disponible, queda como limitación documentada del addon para ese modelo
de heat pump.

## Decisiones registradas (no re-discutir sin razón)

- ✅ Addon Docker, **no** HACS.
- ✅ Mismo repo: `github.com/hirofairlane/genia-air-ha`.
- ✅ Integración HACS muere; queda en `_reference/` por historicidad.
- ✅ Stack: Flask + paho-mqtt + APScheduler + SQLite + PANEL HTML embebido.
- ✅ Tema oscuro idéntico al Energy Optimizer.
- ✅ MQTT Discovery mínima (6 entidades), no las 35.
- ✅ Reglas determinísticas en v0.1, ML/scikit fuera de alcance.
- ✅ `/data/history.db` SQLite, no InfluxDB (autonomía).
- ✅ Dependencia explícita y declarada de `ebusd` addon de LukasGrebe.
- ✅ Auth: `X-Ingress-Path` para todas las rutas + `X-Hass-User` para writes.
- ✅ Sin control de bomba: optimizer influye vía setpoints, alerta en ΔT
  anómalo sin actuar sobre PWM.
