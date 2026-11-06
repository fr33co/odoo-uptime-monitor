# Documentación técnica

Los ejemplos de código corresponden a la rama `20.0`; las diferencias de las otras ramas están en §9.

## 1. Estructura

```
server_uptime_monitor/
├── controllers/main.py           # /status (página pública)
├── data/ir_cron.xml              # cron cada minuto
├── data/mail_template.xml        # alerta, recuperación, vencimiento
├── models/
│   ├── monitor_checks.py         # checks de red, SIN ORM (thread-safe) + registro
│   ├── website_monitor.py        # modelo principal: configuración y orquestación
│   ├── monitor_result.py         # mixin: campos del último resultado
│   ├── monitor_incident.py       # mixin: máquina de estados, alertas, vencimientos
│   ├── monitor_notification.py   # mixin: envío de correos
│   ├── monitor_resources.py      # mixin: agente, umbrales, validez de la BD
│   ├── monitor_stats.py          # mixin: historial y uptime
│   ├── website_monitor_history.py / _groups.py / _contacts.py
├── security/                     # grupos y accesos
├── views/                        # kanban, lista, formulario, gráficas, status
└── tests/
server_resource_agent/
├── controllers/main.py           # GET /server_agent/metrics
├── models/ir_http.py             # cuenta solicitudes en curso (_dispatch)
├── models/inflight.py            # contador entre procesos (archivos por pid)
├── models/metrics.py             # CPU, memoria, discos, validez de la BD
└── tests/
```

Dependencias: `server_uptime_monitor` → `mail` y `requests`; `server_resource_agent` → `base` y `psutil`.

## 2. Flujo de una revisión

```
cron (1 min) check_website_monitor_cron
 ├─ search monitores con next_check vacío o vencido
 ├─ hilo principal: m._check_params()  → dict plano (lee ORM/sudo aquí)
 ├─ ThreadPoolExecutor(max 10): checks.run_all(params)   ← solo red, sin ORM
 └─ hilo principal, por monitor:
      _apply_result(result)  → escribe, incidente, alertas de vencimiento
      _prepare_history_vals()
    create(history_vals) en lote
```

Regla de diseño: **los hilos nunca tocan el ORM**. Todo lo que necesitan (incluida la contraseña, con `sudo`) se copia a un `dict` antes. Un fallo en un monitor se registra (`_logger.exception`) y no detiene a los demás.

## 3. Registro de checks (`monitor_checks.py`)

```python
@register(lambda p: p.get('agent'))
def _run_agent(p):
    return check_agent(p)
```

`register(applies)` agrega `(applies, func)` a `CHECKS`; `run_all(p)` ejecuta los aplicables **en orden de registro** y mezcla sus diccionarios. Para un check nuevo basta registrar una función; el modelo no cambia. Orden actual: HTTP, TCP, SSL (solo HTTP), agente, dominio.

Cada función devuelve valores de campos del monitor. `ok` (bool) lo consume `_apply_result` y se convierte en `status` (`up`/`down`). Las funciones envoltorio buscan la función real por nombre en tiempo de llamada porque las pruebas las parchean en el módulo.

Detalles relevantes:

- **Modo Odoo** (`p['odoo']`): fuerza `GET {origen}/web/login` con `stream=True`, mide `response.elapsed` (hasta cabeceras), lee ≤ 64 KiB y valida marcadores (`/web/assets/`, `__session_info__`, `oe_login_form`, `o_database_list`, `odoo`).
- **Dominio**: RDAP (`https://rdap.org/domain/<dominio>`), una vez al día (`domain_checked_at`). `registered_domain()` aplica la regla de sufijos de segundo nivel; `PLATFORM_DOMAINS` se omiten.
- **Agente**: `GET agent_url` con `Authorization: Bearer <token>`. Fallo = solo `agent_status`; deja intactos los últimos valores de validez de la BD.
- **Validez de la BD** (`database_values`): `expiration_date` reportada manda; si no hay y `estimate_days > 0`, estima `create_date + estimate_days` y marca `db_expiry_estimated`.

## 4. Modelos

### `website.monitor` (hereda `mail.thread` + 4 mixins)

Campos propios: configuración (`name`, `url`, `check_type`, `tcp_port`, `http_method`, `expected_status`, `expected_keyword`, `custom_headers`, `auth_user`, `auth_password`*, `verify_ssl`, `is_odoo`, `check_domain_expiry`, `check_interval`, `timeout`, `failure_threshold`, `max_response_time`, `next_check`, `color` calculado). \* restringido al grupo manager.

Restricciones: intervalo, timeout y umbral > 0; puerto TCP válido en monitores TCP. `@api.onchange('is_odoo')` apaga `agent_enabled` cuando deja de ser Odoo.

Ganchos que sobrescribe: `_extra_problems()` (umbrales de recursos) y `_expiry_sources()` (suma la BD).

### Mixins (una responsabilidad cada uno)

| Mixin | Responsabilidad | Puntos clave |
|---|---|---|
| `website.monitor.result.mixin` | Campos del último resultado | `status`, `last_checked`, `response_time`, `http_*`, `ssl_*`, `domain_*`. |
| `website.monitor.incident.mixin` | Incidentes y vencimientos | `_apply_result`, `_has_problem`, `_open_or_continue_incident`, `_close_incident`, `_check_expiry_alerts`, `parse_days`. |
| `website.monitor.notification.mixin` | Correos | `_notify(kind)` con `kind` ∈ `alert`, `recovery`, `ssl_expiry`, `domain_expiry`, `db_expiry`. |
| `website.monitor.resources.mixin` | Agente y recursos | `_agent_params`, `_resource_problems`, `_db_expiry_sources`, `_db_expiring`, `_resource_history_vals`, `action_view_metric` (abre la gráfica de una métrica). |
| `website.monitor.stats.mixin` | Historial y uptime | `history_count`, `uptime_24h/7d/30d`. |

### Máquina de incidentes

- `_has_problem()`: `status == 'down'`, SSL con error (se toleran `Valid`, `Expiring soon`, `Not HTTPS`), respuesta > `max_response_time`, o `_extra_problems()`.
- Con problema: `consecutive_failures += 1`, fija `problem_since`; al llegar a `failure_threshold` envía **una** alerta (`service_notified`).
- Sin problema: si hubo alerta, calcula minutos de caída y envía recuperación; reinicia contadores.

### Alertas de vencimiento

`_expiry_sources()` devuelve tuplas `(campo_fecha, campo_nivel, tipo_notificación, niveles)`. Para cada una se calcula `days = (fecha - ahora).days` y `level` = el menor nivel configurado que sea ≥ días (0 si todavía falta más que el nivel mayor). Se notifica cuando hay nivel y `previous` está vacío o el nuevo nivel es menor; el nivel guardado (`ssl_alert_level`, `domain_alert_level`, `db_alert_level`) evita repetir y se reinicia al renovar. Añadir una nueva fuente = devolver otra tupla.

### Historial

`website.monitor.history`: una fila por revisión del cron o *Check Now* (`response_time`, `http_status`, `status`, `cpu_percent`, `memory_percent`, `disk_percent`, `requests_in_flight`; agregados `avg`/`max` para gráficas y pivote). `@api.autovacuum` borra las anteriores a `server_uptime_monitor.history_retention_days` (90).

### Grupos y contactos

`website.monitor.groups` (usuarios Odoo + contactos) y `website.monitor.contacts` (correo, teléfono, tipo de notificación).

## 5. Seguridad del monitor

| Elemento | Detalle |
|---|---|
| Grupos | `group_website_monitor_user` (lectura) y `group_website_monitor_manager` (CRUD), bajo la categoría/privilegio *Website Monitor*. |
| Accesos | `security/ir.access.csv` (20) · `ir.model.access.csv` (19, 18, 17). Los 4 modelos tienen lectura para user y CRUD para manager. |
| Campos | `auth_password` y `agent_token` con `groups=manager`; se leen con `sudo()` en `_check_params`/`_agent_params`. |
| `/status` | `auth='public'`; solo nombre, estado y uptime 30d; sin URLs. |
| Cron | Corre como superusuario del cron; las llamadas de red salen del servidor de monitoreo (cuidado con SSRF: solo gestores pueden definir URLs). |

## 6. `server_resource_agent`

### Endpoint

`GET /server_agent/metrics` — `auth='public'`, `csrf=False`, `sitemap=False`, kw de ruta `agent_probe=True`.

Orden de las comprobaciones (cualquier fallo → **404**, para no anunciar el endpoint):

1. `server_resource_agent.allowed_ips` (opcional; IPs/CIDR separados por coma; vacío = todas). Tras un proxy inverso activa `proxy_mode`.
2. Token: `Authorization: Bearer <token>` o `X-Agent-Token`, comparado con `hmac.compare_digest` contra `server_resource_agent.token`. Sin token configurado, siempre 404.

Respuesta (`Cache-Control: no-store`):

```json
{
  "timestamp": 1730000000.0,
  "cpu": {"percent": 12.3, "count": 8, "load_avg": [0.5, 0.4, 0.3]},
  "memory": {"total": 0, "available": 0, "percent": 41.0, "swap_percent": 0.0, "odoo_rss": 0},
  "disks": [{"name": "data_dir", "path": "/var/lib/odoo", "total": 0, "used": 0, "percent": 55.0}],
  "database": {"expiration_date": "2026-12-01 00:00:00", "expiration_reason": "trial", "create_date": "2026-11-01 10:00:00"},
  "requests": {"in_flight": 2, "peak": 5, "workers": 4, "db_active_queries": 1}
}
```

### Métricas

- **CPU**: porcentaje entre dos lecturas (delta de `/proc` guardado en `cpu_sample`; muestra caducada tras 3600 s).
- **Memoria**: RSS del árbol de procesos de Odoo (el padre si hay workers) más memoria del sistema.
- **Discos**: `data_dir` y `/`; se omite uno cuyo `(total, usado)` coincide con otro (mismo disco montado dos veces en Docker).
- **Base de datos**: SQL directo a `ir_config_parameter` (`database.expiration_date`, `database.expiration_reason`, `database.create_date`).

### Solicitudes concurrentes

`ir.http._dispatch` se sobrescribe para llamar a `inflight.enter()/leave()` (salvo rutas con `agent_probe`). Con varios workers un contador en memoria no sirve, así que **cada proceso escribe `actual pico inicio_pico` en su propio archivo** dentro de `$TMP/odoo_resource_agent_<uid>` (directorio `0700`) y el endpoint suma los de procesos vivos. El **pico** se reinicia cuando el endpoint lo lee (archivo `last_read`), de modo que un pico corto entre dos consultas no se pierde. Toda escritura es best-effort: nunca rompe una petición.

### Instalación

`post_init_hook(env)` genera el token (`secrets.token_urlsafe(32)`) si no existe. Para rotarlo, cambia el parámetro de sistema y actualiza el monitor.

## 7. Parámetros de sistema

| Clave | Módulo | Efecto |
|---|---|---|
| `server_uptime_monitor.history_retention_days` | monitor | Días de historial (90). |
| `server_resource_agent.token` | agente | Token de acceso (generado al instalar). |
| `server_resource_agent.allowed_ips` | agente | Lista blanca opcional de IP/CIDR. |

## 8. Pruebas

43 pruebas (`tests/test_website_monitor.py`, `tests/test_agent.py`) que cubren: checks HTTP/TCP/SSL/Odoo/dominio, registro de checks, incidentes y recuperación, niveles de vencimiento, estimación y validación de días, recursos y umbrales, endpoint (token, IP, 404), contador de solicitudes y validez de la BD. Las llamadas de red se parchean en `monitor_checks`.

```
odoo -d test -i server_uptime_monitor,server_resource_agent \
     --test-tags /server_uptime_monitor,/server_resource_agent --stop-after-init
```

Nota: tras escribir con el ORM y antes de leer con SQL directo hay que hacer `env.flush_all()`.

## 9. Diferencias por versión

| Tema | 20.0 | 19.0 | 18.0 | 17.0 |
|---|---|---|---|---|
| Accesos | `ir.access.csv` | `ir.model.access.csv` | igual que 19 | igual que 19 |
| Restricciones SQL | `models.Constraint` | `models.Constraint` | `_sql_constraints` | `_sql_constraints` |
| Grupos | `res.groups.privilege` + `user_ids` | igual | `ir.module.category` + `users` | igual que 18 |
| Parámetros | `get_str/set_str/get_int` | `get_param/set_param` | igual que 19 | igual que 19 |
| Traducción en Python | `self.env._()` | `self.env._()` | `self.env._()` | `from odoo import _` |
| Vistas | `<list>`, kanban `card` | igual | `<list>`, kanban `card` | `<tree>`, `kanban-box`, chatter `div.oe_chatter` |
| Cron | sin `numbercall` | igual | igual | `numbercall = -1` |
| Campos agregables | `aggregator` | `aggregator` | `aggregator` | `group_operator` |

Cómo portar una corrección: aplícala en `20.0` y haz `git cherry-pick` hacia `19.0`, `18.0` y `17.0`; resuelve solo los conflictos de la tabla anterior y ejecuta las pruebas con la imagen oficial `odoo:<versión>`.

## 10. Cómo extender

- **Nuevo tipo de check**: función en `monitor_checks.py` con `@register(lambda p: ...)` que devuelva campos; si necesita ajustes, añade campos al modelo y expónlos en `_check_params`.
- **Nueva fuente de vencimiento**: devuelve otra tupla en `_expiry_sources()` y añade campo de nivel y plantilla/tipo en `_TEMPLATES`.
- **Nueva métrica del agente**: añade la clave en `metrics.collect`, el campo en el mixin de recursos, `_resource_history_vals` y la entrada en `METRICS`.
- **Nuevo canal (p. ej. Telegram)**: implementar en `monitor_notification.py` según `notification_type` del contacto.
