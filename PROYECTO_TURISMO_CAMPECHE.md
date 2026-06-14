# PROYECTO: Guía Turístico Inteligente de Campeche por WhatsApp
### (Brief completo para iniciar un chat nuevo — pegar esto como primer mensaje)

---

## 0. Quién soy y qué quiero
Soy **Chino, de LiTek** (imprenta y publicidad en Campeche, México). Ya tengo un agente de WhatsApp con IA para mi negocio ("Clio") funcionando en producción. Ahora quiero construir un **proyecto NUEVO y SEPARADO**: un **guía turístico inteligente por WhatsApp para el estado de Campeche**, para presentárselo a la Secretaría de Turismo.

> ⚠️ IMPORTANTE: Este proyecto es **independiente de Clio**. NO debe tocar ni afectar a Clio (que vive en otro número con Whapi). Aquí partimos de cero con tecnología propia.

---

## 1. La idea en una frase
Un **guía turístico con Inteligencia Artificial que vive en WhatsApp**: el turista escanea un código QR, se abre WhatsApp, pregunta lo que quiera **en cualquier idioma**, y recibe información, rutas, recomendaciones y cómo llegar — **sin descargar ninguna app**.

## 2. Cómo funciona (flujo)
1. El turista ve un **QR** (aeropuerto, hoteles, centro histórico, fuertes, terminal de autobuses).
2. Lo escanea → se abre WhatsApp con el guía (un wa.me con mensaje pre-llenado).
3. Pregunta con sus palabras, en su idioma.
4. Recibe respuesta + **link de Google Maps** + recomendaciones de negocios locales.

## 3. Qué puede preguntar el turista (y cómo se resuelve)
- "¿Qué puedo hacer hoy?" → lista de actividades según día/clima/hora.
- "¿A qué hora abre el Fuerte de San Miguel?" → horarios y costo + cómo llegar.
- "Dame una ruta turística para hoy" → itinerario por cercanía (mañana/tarde/noche).
- "¿Dónde como cochinita pibil?" → 3-4 restaurantes + ubicación + promoción.
- "How do I get to the historic center?" → responde en inglés + Google Maps.
- "¿Hay tours a Edzná o Calakmul?" → operadores, horarios y contacto.
- "¿Qué hago en la noche?" → bares, malecón, espectáculos, eventos.

> Toda la información la cargamos nosotros (oficial y verificada). La IA elige y responde.

## 4. Conexión con prestadores de servicio (el modelo de negocio)
- Cada **restaurante / hotel / tour** tiene su reseña (a qué se dedica, desde cuándo, qué ofrece).
- Pueden poner **promociones**: "en tu 3ª torta una horchata gratis", "un taco por visitarnos".
- Es **publicidad local inteligente**: el negocio aparece justo cuando el turista pregunta por su giro.
- **Catálogo de productos**: WhatsApp tiene catálogo nativo (foto, precio, descripción, listas, carrito). Cada prestador puede mostrarse como "producto". (Funciona igual que una tienda.)

## 5. Beneficios
**Para el ESTADO:** más derrama económica, inteligencia turística (datos reales), imagen innovadora, sin costo de app, atención multilingüe sin traductores, apoya lo local.
**Para el TURISTA:** todo en un lugar, en su idioma, recomendaciones confiables, cómo llegar, promociones, se siente acompañado.
**Para los PRESTADORES:** visibilidad ante turistas listos para gastar, promociones, estadísticas, sin app propia.

## 6. Inteligencia turística (lo más valioso)
Reportes para Turismo: turistas atendidos por día/mes, de dónde vienen, en qué idioma escriben, qué preguntan más (comida/fuertes/playas/tours), horarios de mayor afluencia.

---

## 7. ARQUITECTURA TÉCNICA (cómo se construye)
Se reutiliza el **mismo motor** que ya tengo en Clio, pero con un cambio CRÍTICO en el canal de WhatsApp:

| Componente | Tecnología |
|---|---|
| Servidor | FastAPI (Python) en Railway, 24/7 |
| Cerebro (IA) | Anthropic Claude (multi-idioma automático) |
| Memoria | PostgreSQL (historial por turista) |
| Conocimiento | Archivos cargados (info turística + prestadores) |
| Panel | CRM web para administrar prestadores, promos y ver datos |
| WhatsApp | **API OFICIAL de Meta (Cloud API)** ← NO Whapi |

> 🔑 **Decisión clave:** A diferencia de Clio (que usa Whapi), este proyecto DEBE usar la **API oficial de Meta Cloud** desde el inicio, con un **número nuevo dedicado**. Razón: escala (miles de personas) y mensajes salientes sin que baneen la cuenta.

## 8. Modelos de IA y costos (precios Anthropic vigentes)
- Claude **Sonnet 4.6**: $3 USD / millón tokens entrada, $15 / millón salida (recomendado: equilibrio calidad/precio).
- Claude **Haiku 4.5**: $1 / $5 (más barato, para preguntas simples).
- Claude **Opus 4.8**: $5 / $25 (máxima calidad).
- Con **prompt caching**, la base de conocimiento se cobra ~90% más barato.
- **Costo por conversación de turista: ~$0.10–0.20 USD (≈ 2–4 pesos).**

**Costo mensual de IA según volumen:**
- 500 turistas/mes (piloto): ~$1,000–1,800 MXN
- 3,000/mes: ~$6,000–11,000 MXN
- 10,000/mes (estado, temporada alta): ~$20,000–36,000 MXN

**Otros costos:** Railway ~$200–800 MXN/mes · dominio ~$200 MXN/año · número dedicado ~$150–300 MXN · WhatsApp entrante (turista escribe primero) **prácticamente gratis** en la API oficial.

**Costo real no-software:** una persona que cargue/actualice contenido y dé de alta prestadores (operación/sueldo).

## 9. WhatsApp oficial — respuestas a las preguntas difíciles
- **¿800+ personas me banean?** Con la API oficial de Meta: **NO**, está hecha para miles. (Con Whapi sí habría riesgo — por eso aquí va la oficial.)
- **¿Catálogo de productos?** **Sí**, WhatsApp tiene catálogo nativo (como una tienda).
- **¿Mensajes a turistas que ya se fueron?** **Sí, pero con reglas:** después de 24h solo se mandan **plantillas aprobadas por Meta**, y solo a quien **aceptó** recibir mensajes (opt-in). Mandar masivo no solicitado = spam = te bajan el alcance y te bloquean. Bien hecho (plantilla + opt-in) = reactivación de turismo recurrente.

## 10. Puntos negros / riesgos honestos
1. La info hay que **mantenerla actualizada** o el guía responde mal.
2. El **costo de IA sube con el volumen** (se cubre con membresías de prestadores).
3. Depende de la **aprobación/verificación de negocio en Meta** (trámite).
4. Guardar datos de turistas = **responsabilidad de privacidad** (aviso de privacidad, ley de datos).

## 11. Implementación por fases
1. **Piloto:** Centro Histórico de Campeche + ~10 prestadores. QR en puntos clave.
2. **Ciudad:** toda la ciudad + más giros (hoteles, tours, transporte).
3. **Estado:** Carmen, Champotón, Calakmul, Edzná, pueblos mágicos.
4. **Futuro:** **voz** (el turista habla por teléfono con el guía).

## 12. Modelo de ingreso (cómo se paga solo)
- **Membresía** mensual de prestadores (ej. $500–1,000 MXN/mes × 30 negocios = $15,000–30,000 MXN/mes).
- **Destacados** (aparecer primero) y **estadísticas** para los negocios.
- Apoyo/convenio con la Secretaría de Turismo.

## 13. Marca (LiTek)
- Colores: **rojo #E30613**, negro, crema/blanco. Lema: "Tu impresión y solución — Piensa Rojo".
- Logo: archivo `knowledge/LITEK LOGO ROJO Y NEGRO-02.png` en el repo de LiTek.
- Ventaja de LiTek: ya tiene el **motor (Clio)** y una **imprenta** para producir los QR y la señalética.

---

## 14. DETALLE TÉCNICO (blueprint de implementación)

### Stack (el mismo que Clio, ya probado en producción)
Python 3.11 + FastAPI + Uvicorn en **Railway** (deploy desde GitHub). Librerías: `anthropic` (SDK de Claude), `SQLAlchemy async + asyncpg` (PostgreSQL), `httpx`, `APScheduler` (tareas programadas), `PyYAML`, `python-dotenv`.

### Lo MÁS importante: el adaptador de Meta YA EXISTE
Clio ya tiene `agent/providers/meta.py` (clase `ProveedorMeta`) que habla con la **API oficial de Meta Cloud**:
- Verificación del webhook (GET con `hub.challenge` y `hub.verify_token`).
- Parseo de mensajes entrantes (`entry → changes → value → messages`).
- Envío vía `graph.facebook.com/v{version}/{phone_number_id}/messages`.
→ Para turismo **se reutiliza tal cual**; no hay que inventarlo.

### Estructura del código (se copia y adapta)
- `main.py` — el webhook responde `200` al instante y procesa en segundo plano (`asyncio.create_task`) para no dar timeout con mucho volumen.
- `brain.py` — llama a Claude con el system prompt de `prompts.yaml`. Modelo `claude-sonnet-4-6` (o Haiku para abaratar).
- `memory.py` — tabla de mensajes (teléfono, rol, contenido, fecha); historial por turista.
- `prompts.yaml` — personalidad + reglas + conocimiento + mensajes de error.
- `knowledge/` — archivos con la info turística.
- `crm.py` (panel) — login por persona (auth token HMAC), tarjetas y estados → se adapta a un panel de **prestadores + consultas + estadísticas**.

### Variables de entorno (Railway)
```
ANTHROPIC_API_KEY=...
WHATSAPP_PROVIDER=meta        # oficial, NO whapi
META_ACCESS_TOKEN=...
META_PHONE_NUMBER_ID=...      # número nuevo dedicado
META_VERIFY_TOKEN=...
DATABASE_URL=postgresql+asyncpg://...
PORT=8000
```

### Lo NUEVO/específico de turismo a construir
1. **Base de conocimiento estructurada:** `lugares` (horario, costo, ubicación + link Maps, descripción), `prestadores` (giro, reseña, promoción, ubicación), `rutas`.
2. **Multi-idioma:** instrucción en el prompt para detectar y responder en el idioma del turista (Claude lo hace solo).
3. **Herramientas (funciones):** `buscar_lugar()`, `recomendar_prestador(giro)`, `generar_ruta()`, `link_maps()`.
4. **Catálogo / mensajes interactivos** de la Cloud API (listas, botones, mensajes de producto).
5. **Plantillas + opt-in** para reactivar turistas (después de 24h solo plantillas aprobadas por Meta).
6. **Requisitos Meta:** cuenta WhatsApp Business (WABA), verificación de negocio, número registrado en Cloud API, webhook suscrito al campo "messages".

---

## 15. ESTADO ACTUAL / SIGUIENTE PASO
- ✅ Ya existe una **presentación en PowerPoint** para Turismo: `Guia_Turistico_Campeche_LiTek.pptx` (13 diapositivas, con logo y colores LiTek). Está en la carpeta del proyecto y en ~/Downloads.
- ⏳ Pendiente de decidir: **nombre del agente turístico** (ej. algo maya/campechano), arrancar el **piloto** en el Centro Histórico, definir los primeros prestadores.

## 16. Lo que quiero del chat nuevo
Ayúdame a llevar este proyecto adelante (es SEPARADO de Clio). Empecemos por lo que yo te diga: puede ser afinar la presentación, montar un **prototipo** del guía con 3-4 lugares de prueba, definir el nombre, o planear el piloto. Háblame siempre en **español**, claro y directo, paso a paso.
