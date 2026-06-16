# 🧾 RECETA PARA DUPLICAR EL CRM (LiTek)
### Guía técnica paso a paso — solo el panel CRM

> El CRM es el panel web donde el equipo ve y atiende a los clientes. Esta receta
> explica de qué está hecho y cómo armar/duplicar uno igual para otro negocio.

---

## 1. ¿QUÉ ES EL CRM (técnicamente)?

Es **una sola página web** servida por un servidor en Python (FastAPI). No usa frameworks
de frontend (nada de React); es **HTML + CSS + JavaScript puro**, todo dentro de un
archivo Python (`agent/crm.py`) como un texto grande.

- El **servidor** (FastAPI) entrega la página y responde a unas "ligas internas" (APIs).
- La **base de datos** (PostgreSQL) guarda los clientes y los usuarios del panel.
- El navegador del equipo pide datos a esas APIs y dibuja las tarjetas.

```
Navegador (equipo)  ⇄  Servidor FastAPI (Python)  ⇄  Base de datos PostgreSQL
     (HTML/CSS/JS)         (agent/main.py + crm.py)        (tabla crm_registros)
```

---

## 2. INGREDIENTES (tecnologías)

| Pieza | Para qué | Costo |
|---|---|---|
| **Python 3.11** | El lenguaje del servidor | gratis |
| **FastAPI + Uvicorn** | El servidor web (entrega la página y las APIs) | gratis |
| **SQLAlchemy (async) + asyncpg** | Hablar con la base de datos | gratis |
| **PostgreSQL** | Guardar clientes y usuarios | incluido en Railway |
| **HTML + CSS + JavaScript** | El panel que se ve | gratis |
| **Railway** | Mantenerlo prendido 24/7 (la nube) | ~$5–20 USD/mes |
| **GitHub** | Guardar el código | gratis |

---

## 3. LAS 3 PARTES DEL CRM

### A) La base de datos — la "libreta"
Una tabla `crm_registros` con una fila por cliente. Columnas principales:
`id, tipo, nombre, telefono, descripcion, asesor, estado, sucursal, alerta, expres,
calificacion, monto, pagado_en, factura, facturado, notas, creado, actualizado`.

Y una tabla `crm_usuarios` para el login: `usuario, password_hash, nombre`.

### B) El servidor — las "ligas internas" (APIs)
En `agent/main.py` viven estas rutas (endpoints):
- `GET /crm` → entrega la página HTML del panel.
- `POST /crm/login` → revisa usuario+contraseña y entrega un **token** de sesión.
- `GET /crm/api/registros` → lista de clientes + estadísticas + ventas + carga.
- `POST /crm/api/registro/{id}` → actualiza una tarjeta (estado, asesor, sucursal, monto, etc.).
- `GET /crm/api/chat` → la conversación de un cliente.
- `POST /crm/api/control` → tomar control / devolver a Clio.
- `POST /crm/api/enviar` → mandar un mensaje al cliente desde el panel.
- `GET /crm/api/usuarios` y `POST /crm/api/usuario/password` → gestionar el equipo.

### C) El panel — la página (en `agent/crm.py`)
Un texto HTML grande con:
- Pantalla de **login**.
- El **tablero**: selector de sucursal, buscador, estadísticas, filtros, y las **tarjetas**.
- El **JavaScript** que pide datos a las APIs y dibuja todo.
- Funciones `crear_token` / `verificar_token` (seguridad).

---

## 4. SEGURIDAD (cómo protege el acceso)

- Las contraseñas NO se guardan en texto: se guardan **cifradas** (hash pbkdf2).
- Al entrar, el servidor entrega un **token firmado (HMAC)** que el navegador manda en
  cada petición. Si el token es falso o viejo, no deja entrar.
- **Permisos por persona:** cada asesor ve solo sus clientes; los jefes ven todo; un
  "admin de sucursal" (ej. Leo) ve solo su sucursal. (Listas `CRM_VEN_TODO`,
  `CRM_USUARIO_A_ASESOR`, `CRM_ADMIN_SUCURSAL` en `main.py`).
- El **dinero** solo lo ven/edita los administradores.

---

## 5. RECETA — PASOS PARA DUPLICARLO

### Paso 1 — Copia el proyecto
Clona el repositorio de GitHub (o copia las carpetas `agent/` y `config/`).

### Paso 2 — Crea las cuentas del nuevo negocio
- **Railway** (la nube) → crea un proyecto nuevo.
- En Railway, agrega un **PostgreSQL** (un clic) → te da la `DATABASE_URL`.
- (Si el CRM va junto con un agente, también: cuenta de Anthropic + Whapi.)

### Paso 3 — Variables de entorno (en Railway)
```
DATABASE_URL=postgresql+asyncpg://...   # te la da Railway
CRM_SECRET=una-clave-larga-secreta      # para firmar los tokens
PORT=8000
```

### Paso 4 — Cambia lo del nuevo negocio
- En `agent/memory.py` → `_crear_usuarios_crm_iniciales()`: pon los **usuarios y
  contraseñas** del nuevo equipo.
- En `agent/main.py`: ajusta las listas de permisos (`CRM_VEN_TODO`,
  `CRM_USUARIO_A_ASESOR`, asesores por sucursal) y los nombres de sucursales si aplica.
- En `agent/crm.py`: cambia el **título, logo y colores** (busca `#e30613` para el color
  y "CRM LiTek" para el nombre).

### Paso 5 — De dónde salen los clientes
El CRM **muestra** datos; alguien tiene que **meterlos**. Dos opciones:
- **(A)** Junto con el agente (Clio): el agente escribe en la misma tabla `crm_registros`
  automáticamente (es lo que hace LiTek).
- **(B)** CRM solo: agregas un formulario o una API para crear clientes a mano / desde otra fuente.

### Paso 6 — Sube a Railway
```
git add .
git commit -m "CRM del nuevo negocio"
git push        # Railway lo despliega solo
```
Entra a `https://tu-proyecto.up.railway.app/crm` → ¡ya está!

---

## 6. PERSONALIZAR PARA OTRO NEGOCIO (checklist)
- [ ] Nombre y logo del panel (`crm.py`).
- [ ] Colores de la marca (`crm.py`, busca el hex del color).
- [ ] Usuarios y contraseñas del equipo (`memory.py`).
- [ ] Estados que usen (nuevo/asignado/proceso/vendido… o los que necesiten).
- [ ] Campos extra de la tarjeta si el negocio los pide (agregar columna + migración).
- [ ] Quién ve qué (permisos en `main.py`).

---

## 7. LO MÍNIMO QUE NECESITAS SABER / TENER
- **Python básico** (o un asistente que programe los cambios).
- Cuentas: **Railway** (nube + base de datos) y **GitHub** (código).
- Si el CRM va con un agente de WhatsApp: además **Anthropic** + **Whapi/Meta**.

> El concepto, la estructura y el código YA existen (plantilla AgentKit). Duplicar = copiar
> + cambiar configuración + conectar la base de datos. No se programa desde cero.

---

## 8. RESUMEN EN UNA FRASE
> El CRM es **una página web (HTML/CSS/JS) servida por un servidor Python (FastAPI)**,
> guardada en **PostgreSQL**, alojada en **Railway**. Para duplicarlo: copias el código,
> cambias la configuración (usuarios, marca, permisos), conectas una base de datos nueva,
> y lo subes a Railway.
