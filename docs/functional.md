# Guía funcional

## 1. Qué problema resuelve

- Saber cuándo un sitio o servicio **cae**, **se pone lento** o está a punto de **vencer** (certificado SSL, dominio, base de datos demo/trial de Odoo).
- Saber cuándo una **instancia Odoo** está sobrecargada (CPU, memoria, disco, solicitudes concurrentes) sin entrar al servidor.
- Avisar por correo a las personas correctas, **una sola vez por incidente**, y avisar también cuando se recupera.

## 2. Roles

| Grupo (*Ajustes ▸ Usuarios ▸ Website Monitor*) | Puede |
|---|---|
| **User** | Ver monitores, grupos, contactos e historial. |
| **Administrator** | Todo lo anterior, crear/editar/borrar y ver el menú *Configuration*, la contraseña de autenticación básica y el token del agente. |

## 3. Menús

- **Monitored Websites**: kanban agrupado por estado (verde sano, amarillo con advertencias, rojo caído), lista y formulario.
- **Reporting ▸ History**: gráfica de líneas, lista y pivote del historial de cada revisión.
- **Configuration ▸ Groups / Contacts**: a quién se avisa.

## 4. Configurar a quién se avisa

1. **Groups**: crea un grupo (p. ej. "Soporte"). Añade *usuarios Odoo* (se usa el correo de su contacto) y/o *contactos externos* (solo correo).
2. Cada monitor pertenece a un grupo (*Notify Group*).

> Los contactos de tipo Telegram se pueden guardar, pero todavía no se envía ningún mensaje por ese medio.

## 5. Crear un monitor

Campos básicos: **Nombre**, **URL / Host**, **Tipo de revisión** (HTTP o puerto TCP), **Grupo a notificar**, y *Show on Status Page* si debe salir en la página pública.

Pestaña **Check Settings**:

| Sección | Campo | Significado |
|---|---|---|
| Schedule | Check Every (min) | Cada cuánto se revisa (por defecto 5). |
| | Timeout (s) | Tiempo máximo de espera. |
| | Failures Before Alert | Fallos seguidos antes de alertar (por defecto 2). Evita falsas alarmas. |
| | Slow Threshold (ms) | Una respuesta más lenta que esto cuenta como problema (por defecto 4000). |
| HTTP | Odoo Instance | Mide como lo necesita una instancia Odoo (ver §6). |
| | Method / Expected Status / Expected Keyword | Método GET/HEAD, código esperado (0 = cualquiera menor a 500), texto que debe contener la respuesta. |
| | Verify SSL | Valida el certificado. |
| | Check Domain Expiry | Consulta el vencimiento del dominio (RDAP, una vez al día). |
| | Warn SSL / Domain Expiry (days) | Niveles de aviso, por defecto `30,15,7,1`. |
| Authentication and Headers | Usuario/contraseña básica, cabeceras | Para sitios protegidos. |
| Server Resources | (ver §7) | Solo si *Odoo Instance* está marcado. |

Botón **Check Now**: revisa de inmediato y guarda una fila de historial.

## 6. Instancias Odoo (*Odoo Instance*)

Pensado para Odoo.sh u Odoo online, donde interesa saber si la instancia está lenta sin cargarla:

- Hace **una sola** petición anónima a `/web/login` del sitio (la página la genera un worker y necesita la base de datos, así que su latencia refleja la carga real).
- Mide solo el tiempo hasta recibir las cabeceras y lee como máximo 64 KiB para confirmar que **la respuesta es realmente Odoo** (un 404 o un sitio ajeno no se reporta como sano).
- Combínalo con *Slow Threshold* y *Failures Before Alert* para recibir aviso solo si la lentitud persiste. Usa un intervalo de 5 minutos o más.

## 7. Recursos del servidor y validez de la base de datos

Solo para servidores Odoo que **controlas** (no aplica a Odoo Online / Odoo.sh, donde no se pueden instalar módulos que lean el host).

1. En el servidor vigilado instala `server_resource_agent`. Al instalarse genera un token aleatorio en el parámetro de sistema `server_resource_agent.token`.
2. En el monitor: marca *Odoo Instance* y *Server Resources*, pega el token en *Agent Token*. *Agent URL* es opcional (por defecto `<origen del sitio>/server_agent/metrics`; obligatoria en monitores TCP).
3. Ajusta los umbrales: *Max CPU/Memory/Storage (%)* (90 por defecto) y *Max Concurrent Requests* (0 = desactivado).

Qué se obtiene:

- En el formulario, botones con **CPU, memoria, almacenamiento y solicitudes concurrentes**; cada uno abre una **gráfica en el tiempo**. Si el servidor no es Odoo, estas gráficas y datos no se muestran.
- Un umbral superado cuenta como problema y dispara el flujo normal de alertas.
- Si el agente no responde, se muestra en *Agent Status*, pero **no** se considera caída del sitio.
- *Solicitudes concurrentes* es el **pico** entre dos revisiones, para no perder picos cortos.

### Validez de la base de datos (demos y trials)

Las demos y pruebas de Odoo vencen y suelen no avisar. El agente lee la fecha que Odoo guarda y el monitor la muestra en la pestaña *Status*:

- Si la base reporta `expiration_date`, **esa fecha manda**.
- Si no reporta ninguna, se **estima** como *fecha de creación + Estimate Expiry After (days)* (30 por defecto) y se marca como *Estimated Expiry* en pantalla y en el correo. Ponlo en 0 para desactivar la estimación (bases que no vencen).
- Se avisa por correo **5 días antes** por defecto; se configura en *Warn Database Expiry (days)* (p. ej. `30,15,7,5,1`; añade `1` para un último aviso).
- La tarjeta kanban pasa a amarillo cuando faltan 7 días o menos.

> La fecha puede estar desactualizada hasta que Odoo se sincronice con su editor. El "un mes" que muestra *Ajustes* es solo un valor provisional (hoy + 30 días).

## 8. Correos que se envían

| Correo | Cuándo |
|---|---|
| **Alerta** | Al llegar a *Failures Before Alert* problemas consecutivos (caído, estado inesperado, falta la palabra clave, error SSL, lentitud, recurso sobre el umbral). Una vez por incidente. |
| **Recuperación** | Cuando el monitor vuelve a estar sano; indica los minutos de caída. |
| **Vencimiento** | SSL, dominio o base de datos: una vez al cruzar cada nivel de días configurado, y cuando ya venció. |

Si el servidor es Odoo, el correo de alerta incluye los recursos. Los correos están traducidos al español (`es_ES`).

## 9. Estados y colores

| Color | Significa |
|---|---|
| Gris | Aún no revisado. |
| Verde | Arriba y sin problemas. |
| Amarillo | Arriba pero con advertencia: lento, recurso alto, SSL por vencer o base de datos por vencer. |
| Rojo | Caído. |

## 10. Historial y purga

Cada revisión programada guarda una fila (tiempo de respuesta, estado HTTP y, si aplica, recursos). Se purgan automáticamente las de más de **90 días**; se cambia con el parámetro de sistema `server_uptime_monitor.history_retention_days`.

## 11. Página pública de estado

`/status` lista los monitores con *Show on Status Page*: solo nombre, estado y uptime de 30 días. **Nunca expone URLs.**

## 12. Limitaciones conocidas

- Telegram aún no envía mensajes.
- El dominio se reconoce con una regla simple de sufijos de segundo nivel (`.com.co`, `.co.uk`, `.gob.mx`…), no con la lista pública completa. Los dominios de la plataforma (`odoo.com`, `odoo.sh`, `odoo.dev`) se omiten a propósito.
- El eje de la gráfica (`check_label`) está en UTC.
- La entrega depende del servidor de correo saliente de la base.

## 13. Problemas frecuentes

| Síntoma | Causa / solución |
|---|---|
| Las alertas no llegan | Falta servidor de correo saliente, o el grupo no tiene contactos con correo. Revisa *Ajustes ▸ Técnico ▸ Correos*. |
| *Agent Status* = `HTTP 404 (check URL and token)` | Token incorrecto, IP fuera de `allowed_ips`, módulo no instalado o URL equivocada (el agente responde 404 a propósito). |
| *Agent Status* = `Error: …` | Sin conexión al servidor, certificado inválido (revisa *Verify SSL*) o respuesta no JSON. |
| El vencimiento de dominio queda vacío | Dominio de plataforma, IP, o el registro no publica fecha (`domain_status` lo explica). |
| Tras actualizar el módulo, error "no es una acción válida" | Reinicia el servicio de Odoo **antes** de actualizar para cargar el código nuevo. |
