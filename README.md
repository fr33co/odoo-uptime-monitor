# Módulos de monitoreo de servidores para Odoo

Dos addons para vigilar sitios web y servidores Odoo. Hay **una rama por versión
de Odoo**: `20.0`, `19.0`, `18.0` y `17.0`.

| Módulo | Dónde se instala | Para qué sirve |
|---|---|---|
| `server_uptime_monitor` | El Odoo que hace el monitoreo | Revisa sitios web y servicios TCP (disponibilidad, tiempo de respuesta, SSL, vencimiento de dominio), mide lentitud de instancias Odoo, lee recursos del servidor y validez de la base de datos, envía alertas por correo y publica una página de estado. |
| `server_resource_agent` | Cada servidor Odoo que quieras vigilar | Expone `/server_agent/metrics`, un endpoint de solo lectura protegido con token: CPU, memoria, almacenamiento, solicitudes concurrentes y validez de la base de datos. |

```
┌──────────── Odoo de monitoreo ───────────┐         ┌──────── Servidor Odoo vigilado ───────┐
│ server_uptime_monitor                    │  HTTPS  │ server_resource_agent                 │
│  cron cada minuto → hilos → checks ──────┼────────►│  GET /server_agent/metrics (token)    │
│  historial, alertas, página /status      │         │  CPU · memoria · disco · concurrencia │
└──────────────────────────────────────────┘         └───────────────────────────────────────┘
```

## Documentación

- [Guía funcional](docs/funcional.md): qué hace, cómo se configura y se usa, qué alertas llegan.
- [Documentación técnica](docs/tecnica.md): arquitectura, modelos, endpoint del agente, seguridad, pruebas y portado entre versiones.
- READMEs de cada módulo (formato Odoo, en inglés): `server_uptime_monitor/README.rst` y `server_resource_agent/README.rst`.

## Inicio rápido

1. En el Odoo de monitoreo instala `server_uptime_monitor` (requiere el paquete Python `requests`).
2. *Website Monitor ▸ Configuration ▸ Groups*: crea un grupo con los usuarios o contactos que recibirán los correos.
3. *Website Monitor ▸ Monitored Websites*: crea un monitor con nombre, URL y grupo.
4. Para un servidor Odoo propio: marca *Odoo Instance*; si además quieres recursos, instala `server_resource_agent` allí (requiere `psutil`), copia el token del parámetro de sistema `server_resource_agent.token` y actívalo en la pestaña *Check Settings ▸ Server Resources*.
5. Verifica que el servidor de correo saliente de Odoo funcione: sin él las alertas quedan en cola.

## Versiones

| Rama | Diferencias principales |
|---|---|
| `20.0` | Base del desarrollo (`ir.access.csv`, `models.Constraint`, `env._`, `get_str/set_str`). |
| `19.0` | `ir.model.access.csv`, `get_param/set_param`. |
| `18.0` | `_sql_constraints`; grupos con categoría (sin `res.groups.privilege`). |
| `17.0` | `_()` importado, vistas `tree`, `kanban-box`, chatter como `div`, `numbercall` en el cron, `group_operator`. |

Las correcciones se hacen primero en `20.0` y se portan hacia abajo
(`20.0 → 19.0 → 18.0 → 17.0`).

## Pruebas

```
odoo -d <bd> -i server_uptime_monitor,server_resource_agent \
     --test-tags /server_uptime_monitor,/server_resource_agent --stop-after-init
```

43 pruebas, todas en verde en las cuatro versiones.
