# 🤖 GUÍA COMPLETA — Clio (agente de WhatsApp) + CRM
### Cómo está hecho, por qué funciona así, y cómo duplicarlo

> Sistema para que un negocio atienda su WhatsApp con Inteligencia Artificial:
> un agente ("Clio") responde y vende solo, y un panel (CRM) deja al equipo ver y
> dar seguimiento a todo. Esta guía sirve para entenderlo y replicarlo en otro negocio.

---

## PARTE 1 — ¿QUÉ ES? (en una frase)

Son **dos cosas que trabajan juntas**:
1. **Clio** = un empleado virtual con IA que atiende el WhatsApp (responde, cotiza, toma pedidos, da seguimiento).
2. **CRM** = un panel web donde el equipo ve a cada cliente, su estado, el dinero y las pláticas.

Ambos comparten la **misma base de datos**: Clio anota, el equipo consulta.

---

## PARTE 2 — CÓMO FUNCIONA (el camino de un mensaje)

```
Cliente escribe en WhatsApp
   ↓
Whapi (el "cable" a WhatsApp) manda el mensaje al servidor
   ↓
Servidor (FastAPI/Python) lo recibe en /webhook
   ↓  → responde "200 OK" AL INSTANTE y procesa en segundo plano
Memoria (base de datos) saca el historial de ese cliente
   ↓
Cerebro (Claude IA) decide qué responder usando: personalidad + reglas + historial
   ↓  (si hace falta) Calculadora (tools.py) saca el precio exacto
Se guarda en la memoria + se anota/actualiza en el CRM
   ↓
Whapi manda la respuesta de vuelta al cliente
```

El **CRM** corre en el mismo servidor: es una página web que el equipo abre en el
navegador y que pide datos a unas "ligas internas" (APIs).

---

## PARTE 3 — DE QUÉ ESTÁ HECHO (tecnologías)

| Pieza | Tecnología | Por qué se eligió |
|---|---|---|
| Lenguaje principal | **Python 3.11** | El estándar para IA, fácil y con mucha ayuda |
| Servidor web | **FastAPI + Uvicorn** | Rápido y simple para el webhook y las APIs |
| Inteligencia (IA) | **Anthropic Claude** | Entiende y responde como persona, en cualquier idioma |
| Memoria / datos | **PostgreSQL + SQLAlchemy** | Guarda todo y sobrevive reinicios |
| WhatsApp | **Whapi.cloud** (o Meta Cloud API) | Conexión fácil, sin migrar el número |
| Panel CRM | **HTML + CSS + JavaScript** (en un archivo) | Simple, sin frameworks pesados |
| Tareas programadas | **APScheduler** | Recordatorios, reportes, etc. a sus horas |
| Nube (hosting) | **Railway** | Despliega solo desde GitHub, barato |
| Código / respaldo | **GitHub** | Guarda versiones y respalda |

---

## PARTE 4 — LOS ARCHIVOS (qué hace cada uno)

```
agent/
  main.py            ← el "portero": recibe WhatsApp + sirve el CRM + las APIs
  brain.py           ← el "cerebro": habla con la IA (Claude)
  memory.py          ← la "memoria": base de datos (clientes, usuarios, pláticas)
  tools.py           ← la "calculadora": precios exactos por m²/volumen/promos
  crm.py             ← el "panel web" (HTML+CSS+JS, login y tablero)
  escalation.py      ← pasa el cliente con el asesor correcto
  seguimiento.py     ← recordatorio a clientes que no contestan
  agradecimiento.py  ← "gracias + calificación" tras la venta
  reporte_diario.py  ← reporte diario al dueño
  providers/
    whapi.py / meta.py ← los "cables" a WhatsApp (se elige uno)
config/
  prompts.yaml       ← la PERSONALIDAD y reglas de Clio (su "manual")
  business.yaml      ← datos del negocio
knowledge/           ← info del negocio (precios, FAQ, imágenes)
```

---

## PARTE 5 — POR QUÉ FUNCIONA ASÍ (las decisiones clave)

1. **Por WhatsApp, no una app.** Todos ya tienen WhatsApp → cero fricción, nadie descarga nada.
2. **IA real (Claude), no un bot de menús.** Entiende lo que el cliente escribe, en su idioma, y responde natural.
3. **Responde "200 OK" al instante y procesa aparte.** WhatsApp reintenta si tardas; por eso contestamos rápido al webhook y pensamos la respuesta en segundo plano. Evita mensajes duplicados.
4. **La personalidad va en un archivo aparte (`prompts.yaml`), no en el código.** Así se cambia el tono, reglas o precios SIN tocar programación.
5. **Una calculadora separada para los precios (`tools.py`).** La IA podría equivocarse con números; mejor que la matemática la haga código exacto. La IA solo decide CUÁNDO calcular.
6. **Memoria en base de datos (PostgreSQL).** Recuerda cada plática aunque el servidor se reinicie.
7. **El CRM comparte la misma base de datos que Clio.** Por eso lo que pasa en WhatsApp aparece solo en el panel — sin copiar nada a mano.
8. **El CRM es un solo archivo (HTML/CSS/JS).** Simple de mantener, no necesita un proyecto de frontend aparte.
9. **Permisos por persona (tokens).** Cada asesor ve lo suyo; los jefes ven todo y el dinero. Seguro y ordenado.
10. **Railway + GitHub.** Subes el código a GitHub y Railway lo despliega solo. Actualizar = un "push".

---

## PARTE 6 — RECETA PARA DUPLICARLO (otro negocio)

Está hecho como **plantilla**: no se programa de cero, se copia y se cambia la config.

### Paso 1 — Copia el código
Clona el repositorio de GitHub.

### Paso 2 — Crea las cuentas del nuevo negocio
- **Anthropic** (la IA) → saca una API Key.
- **Whapi.cloud** (WhatsApp) → conecta su número, saca el token. (O Meta Cloud API.)
- **Railway** (la nube) → nuevo proyecto + agrega PostgreSQL (un clic).
- **GitHub** → para su copia del código.

### Paso 3 — Cambia lo del negocio (3 cosas)
- `config/prompts.yaml` → personalidad, reglas, tono del nuevo negocio.
- `knowledge/` → sus precios, FAQ, info.
- `agent/tools.py` → sus fórmulas de precio (si vende por medida/volumen).
- (CRM) `agent/memory.py` → usuarios del equipo; `agent/crm.py` → nombre, logo, colores.

### Paso 4 — Variables de entorno (en Railway)
```
ANTHROPIC_API_KEY=...        # la IA
WHATSAPP_PROVIDER=whapi      # whapi | meta
WHAPI_TOKEN=...              # su WhatsApp
DATABASE_URL=postgresql+asyncpg://...   # la da Railway
CRM_SECRET=clave-larga-secreta
PORT=8000
```

### Paso 5 — Sube y despliega
```
git add .
git commit -m "Agente del nuevo negocio"
git push        # Railway lo despliega solo
```

### Paso 6 — Conecta el webhook de WhatsApp
En Whapi (o Meta): pon la URL del webhook → `https://su-proyecto.up.railway.app/webhook`.

### ✅ Listo
- Clientes escriben → Clio responde.
- El equipo entra a `.../crm` y ve todo.

---

## PARTE 7 — COSTOS APROXIMADOS (mensual)

| Servicio | Costo |
|---|---|
| IA (Anthropic) | Por uso. ~$0.02–0.05 por conversación (se controla con límite de gasto) |
| WhatsApp (Whapi) | ~$30–60 USD/mes por número (o Meta: entrantes casi gratis) |
| Railway (nube + base de datos) | ~$5–20 USD/mes |
| GitHub | Gratis |

> ⚠️ Pon un **límite de gasto** en Anthropic para no llevarte sorpresas (a LiTek se le
> topó una vez por hacer muchas pruebas).

---

## PARTE 8 — LO QUE NECESITA TU AMIGO

- **Python básico** o un programador/asistente que haga los cambios.
- Saber de **APIs**, **GitHub** y **Railway** (subir y desplegar).
- Las **cuentas** de arriba (Anthropic, Whapi/Meta, Railway).
- El **contenido del negocio** (precios, reglas, tono) — eso lo más importante.

> Lo difícil (la estructura y el motor) **ya está resuelto**. Replicar = copiar + configurar + conectar.

---

## RESUMEN EN UNA FRASE
> **Clio** (Python + Claude IA + Whapi + PostgreSQL, en Railway) atiende el WhatsApp solo;
> el **CRM** (una página HTML servida por el mismo Python, sobre la misma base de datos)
> deja al equipo verlo y atenderlo. Se duplica copiando el código y cambiando la
> configuración del nuevo negocio. **El motor es el mismo; solo cambia la personalidad y los datos.**
