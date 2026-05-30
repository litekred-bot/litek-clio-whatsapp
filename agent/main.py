# agent/main.py — Servidor FastAPI + Webhook de WhatsApp
# Generado por AgentKit para LiTek

import os
import logging
from contextlib import asynccontextmanager
from collections import OrderedDict
from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import PlainTextResponse, RedirectResponse
from fastapi.middleware.cors import CORSMiddleware
from dotenv import load_dotenv
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from zoneinfo import ZoneInfo

# Cache de IDs procesados para evitar duplicados (Whapi reintenta si tardamos)
# OrderedDict para poder limpiar los más viejos y no crecer infinitamente
_ids_procesados: OrderedDict[str, bool] = OrderedDict()
MAX_IDS_CACHE = 500

from agent.brain import generar_respuesta
from agent.memory import inicializar_db, guardar_mensaje, obtener_historial, registrar_ruleta, verificar_ruleta
from agent.providers import obtener_proveedor
from agent.transcription import transcribir_audio
from agent.document_reader import leer_documento
from agent.escalation import enviar_alerta_asesor, AREAS
from agent.reporte_diario import generar_reporte_diario
from agent.seguimiento import enviar_seguimientos, cargar_cache_desde_db

# Número del dueño para recibir copia de cotizaciones de productos que no son lonas
ADMIN_WHATSAPP = os.getenv("ADMIN_WHATSAPP", "529812710000")
# Número del asesor (Anna) para recibir pedidos confirmados + comprobantes de pago
ASESOR_WHATSAPP = os.getenv("ASESOR_WHATSAPP", "529818290272")

load_dotenv()

ENVIRONMENT = os.getenv("ENVIRONMENT", "development")
log_level = logging.DEBUG if ENVIRONMENT == "development" else logging.INFO
logging.basicConfig(level=log_level)
logger = logging.getLogger("agentkit")

proveedor = obtener_proveedor()
PORT = int(os.getenv("PORT", 8000))


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Inicializa la base de datos y el scheduler al arrancar el servidor."""
    await inicializar_db()
    logger.info("Base de datos inicializada")
    await cargar_cache_desde_db()  # Carga seguimientos recientes para no reenviar tras reinicio
    logger.info(f"Servidor AgentKit — LiTek corriendo en puerto {PORT}")
    logger.info(f"Proveedor de WhatsApp: {proveedor.__class__.__name__}")

    # Scheduler: reporte diario a las 6:00 AM hora Campeche
    scheduler = AsyncIOScheduler(timezone=ZoneInfo("America/Merida"))
    scheduler.add_job(
        generar_reporte_diario,
        CronTrigger(hour=5, minute=0),
        args=[proveedor.token],
        id="reporte_diario",
        name="Reporte diario de clientes pendientes",
        replace_existing=True,
    )
    # ⚠️ SEGUIMIENTO DESACTIVADO — la DB de Railway es efímera, el marcador de
    # "ya envié seguimiento" se pierde en cada reinicio y reenvía cada 30 min (spam).
    # NO reactivar hasta tener volumen persistente o PostgreSQL en Railway.
    # scheduler.add_job(
    #     enviar_seguimientos,
    #     "interval",
    #     minutes=30,
    #     args=[proveedor.token],
    #     id="seguimiento_inactivos",
    #     name="Seguimiento clientes inactivos cada 30 minutos",
    #     replace_existing=True,
    # )
    scheduler.start()
    logger.info("Scheduler iniciado — solo reporte diario 5:00 AM (Campeche)")

    yield

    scheduler.shutdown()
    logger.info("Scheduler detenido")


app = FastAPI(
    title="Clio — Agente de Ventas LiTek",
    version="1.0.0",
    lifespan=lifespan
)

# CORS — permite llamadas desde litek.mx a los endpoints de ruleta
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/")
async def health_check():
    """Endpoint de salud para Railway/monitoreo."""
    return {"status": "ok", "service": "agentkit-litek", "agente": "Clio"}


@app.get("/ruleta/ping")
async def ruleta_ping(nombre: str = "", telefono: str = "", premio: str = "", descripcion: str = ""):
    """
    Endpoint GET sin preflight CORS — llamado via Image pixel desde la ruleta.
    Registra al ganador y manda WhatsApp automáticamente.
    """
    import httpx as _httpx
    if not all([nombre, telefono, premio]):
        return {"ok": False}

    tel = telefono.replace("+", "").replace(" ", "").replace("-", "")
    if len(tel) == 10:
        tel_wa = f"521{tel}"
    elif tel.startswith("52") and len(tel) == 12:
        tel_wa = f"521{tel[2:]}"
    elif tel.startswith("521") and len(tel) == 13:
        tel_wa = tel
    else:
        tel_wa = f"521{tel[-10:]}"

    ok = await registrar_ruleta(telefono, nombre, premio, descripcion)
    if not ok:
        return {"ok": False, "razon": "ya_jugo"}

    msg_wa = (
        f"¡Hola {nombre}! 🎡 Ganaste en nuestra ruleta LiTek: *{premio}* 🎉\n\n"
        f"¿Te gustaría aprovechar y ordenar algo? 😊"
    )
    await guardar_mensaje(tel_wa, "assistant", msg_wa)

    # Mensaje al cliente ganador
    try:
        async with _httpx.AsyncClient(timeout=10.0) as client:
            await client.post(
                "https://gate.whapi.cloud/messages/text",
                json={"to": tel_wa, "body": msg_wa},
                headers={"Authorization": f"Bearer {proveedor.token}", "Content-Type": "application/json"},
            )
            logger.info(f"WhatsApp ruleta enviado a ganador {tel_wa}")
    except Exception as e:
        logger.error(f"Error WhatsApp ruleta ganador: {e}")

    # Copia al admin — registro de cupón con datos del ganador
    if ADMIN_WHATSAPP:
        msg_admin = (
            f"🎡 *RULETA — NUEVO GANADOR*\n\n"
            f"👤 *Nombre:* {nombre}\n"
            f"📱 *Teléfono:* {telefono}\n"
            f"🎁 *Premio:* {premio}\n"
            f"📝 {descripcion}"
        )
        try:
            async with _httpx.AsyncClient(timeout=10.0) as client:
                await client.post(
                    "https://gate.whapi.cloud/messages/text",
                    json={"to": ADMIN_WHATSAPP, "body": msg_admin},
                    headers={"Authorization": f"Bearer {proveedor.token}", "Content-Type": "application/json"},
                )
                logger.info(f"Copia ruleta enviada al admin: {nombre} / {telefono} / {premio}")
        except Exception as e:
            logger.error(f"Error copia ruleta admin: {e}")

    return {"ok": True}


@app.get("/anuncio")
async def redirigir_anuncio():
    """
    Redirige al WhatsApp de Clio desde anuncios de Facebook/Instagram.
    Usamos esta URL en los anuncios para evitar que Facebook bloquee el link directo de wa.me.
    El cliente llega con el mensaje 'vi su anuncio en Facebook' y Clio lo atiende automáticamente.
    """
    url_whatsapp = "https://wa.me/5219845576964?text=Hola%20LiTek%2C%20vi%20su%20anuncio%20en%20Facebook"
    logger.info("Redirigiendo visita de anuncio Facebook → WhatsApp Clio")
    return RedirectResponse(url=url_whatsapp, status_code=302)


@app.post("/reporte")
async def disparar_reporte(request: Request):
    """Dispara el reporte diario manualmente (para pruebas o uso bajo demanda)."""
    ok = await generar_reporte_diario(proveedor.token)
    return {"status": "ok" if ok else "error", "mensaje": "Reporte enviado al grupo" if ok else "Error al enviar"}


@app.post("/ruleta/verificar")
async def ruleta_verificar(request: Request):
    """Verifica si un número de teléfono ya participó en la ruleta."""
    data = await request.json()
    telefono = data.get("telefono", "").strip().replace("+", "").replace(" ", "")
    if not telefono:
        return {"ya_jugo": False}
    resultado = await verificar_ruleta(telefono)
    if resultado:
        return {
            "ya_jugo": True,
            "premio": resultado["premio"],
            "dias_faltantes": resultado.get("dias_faltantes", 0),
        }
    return {"ya_jugo": False}


@app.post("/ruleta/ganador")
async def ruleta_ganador(request: Request):
    """Registra al ganador de la ruleta y le manda WhatsApp con su premio."""
    import httpx as _httpx
    data = await request.json()
    nombre     = data.get("nombre", "").strip()
    telefono   = data.get("telefono", "").strip()
    premio     = data.get("premio", "").strip()
    descripcion = data.get("descripcion", "").strip()

    if not all([nombre, telefono, premio]):
        raise HTTPException(status_code=400, detail="Faltan datos")

    # Normalizar teléfono a formato Whapi México (521XXXXXXXXXX)
    tel = telefono.replace("+", "").replace(" ", "").replace("-", "")
    if len(tel) == 10:
        tel_wa = f"521{tel}"
    elif tel.startswith("52") and len(tel) == 12:
        tel_wa = f"521{tel[2:]}"
    elif tel.startswith("521") and len(tel) == 13:
        tel_wa = tel
    else:
        tel_wa = f"521{tel[-10:]}"

    # Registrar en DB (retorna False si ya participó)
    ok = await registrar_ruleta(telefono, nombre, premio, descripcion)
    if not ok:
        return {"ok": False, "mensaje": "Este número ya participó"}

    # Guardar mensaje en historial para que Clio tenga contexto
    msg_wa = (
        f"¡Hola {nombre}! 🎡 Ganaste en nuestra ruleta LiTek: *{premio}* 🎉\n\n"
        f"¿Te gustaría aprovechar y ordenar algo? 😊"
    )
    await guardar_mensaje(tel_wa, "assistant", msg_wa)

    # Enviar WhatsApp via Whapi
    try:
        async with _httpx.AsyncClient(timeout=10.0) as client:
            r = await client.post(
                "https://gate.whapi.cloud/messages/text",
                json={"to": tel_wa, "body": msg_wa},
                headers={
                    "Authorization": f"Bearer {proveedor.token}",
                    "Content-Type": "application/json",
                },
            )
            logger.info(f"WhatsApp ruleta enviado a {tel_wa}: {r.status_code}")
    except Exception as e:
        logger.error(f"Error enviando WhatsApp ruleta: {e}")

    return {"ok": True}


@app.get("/webhook")
async def webhook_verificacion(request: Request):
    """Verificación GET del webhook (requerido por Meta Cloud API, no-op para Whapi)."""
    resultado = await proveedor.validar_webhook(request)
    if resultado is not None:
        return PlainTextResponse(str(resultado))
    return {"status": "ok"}


@app.post("/webhook")
async def webhook_handler(request: Request):
    """
    Recibe mensajes de WhatsApp via el proveedor configurado.
    Procesa el mensaje, genera respuesta con Claude y la envía de vuelta.
    """
    try:
        mensajes = await proveedor.parsear_webhook(request)

        for msg in mensajes:
            if msg.es_propio:
                continue

            # Deduplicación: ignorar mensajes ya procesados (Whapi reintenta webhooks)
            if msg.mensaje_id and msg.mensaje_id in _ids_procesados:
                logger.info(f"Mensaje duplicado ignorado: {msg.mensaje_id}")
                continue
            if msg.mensaje_id:
                _ids_procesados[msg.mensaje_id] = True
                if len(_ids_procesados) > MAX_IDS_CACHE:
                    _ids_procesados.popitem(last=False)  # elimina el más viejo

            # Guardar media y tipo ORIGINALES antes de modificarlos (para reenviar comprobantes)
            media_original = msg.media_url
            tipo_original = msg.tipo

            # Si es nota de voz, transcribir primero
            if msg.tipo in ("audio", "voice", "ptt") and msg.media_url:
                logger.info(f"Nota de voz de {msg.telefono} — transcribiendo...")
                msg.texto = await transcribir_audio(msg.media_url, proveedor.token)
                logger.info(f"Transcripción: {msg.texto}")

            # Si es documento, extraer texto
            elif msg.tipo == "document" and msg.media_url:
                logger.info(f"Documento de {msg.telefono} — extrayendo texto...")
                texto_doc = await leer_documento(msg.media_url, proveedor.token, msg.texto)
                msg.texto = texto_doc
                msg.tipo = "text"  # Procesar como texto normal con el contenido extraído

            if not msg.texto:
                continue

            logger.info(f"Mensaje de {msg.telefono} [{msg.tipo}]: {msg.texto}")

            # Obtener historial ANTES de guardar el mensaje actual
            historial = await obtener_historial(msg.telefono)
            es_primer_mensaje = len(historial) == 0

            # Generar respuesta con Claude (con soporte de imagen si aplica)
            respuesta = await generar_respuesta(
                msg.texto,
                historial,
                media_url=msg.media_url,
                whapi_token=proveedor.token,
                tipo=msg.tipo,
                nombre_perfil=msg.nombre_perfil,
            )

            # Guardar usuario y respuesta en memoria
            # Si era imagen, guardar con contexto para que no se pierda en turnos siguientes
            if msg.tipo == "image" and msg.media_url:
                texto_guardado = f"[El cliente envió una imagen. Descripción/caption: '{msg.texto}'. Clio analizó la imagen con visión.]"
            else:
                texto_guardado = msg.texto
            await guardar_mensaje(msg.telefono, "user", texto_guardado)
            await guardar_mensaje(msg.telefono, "assistant", respuesta)

            import re

            # Detectar escalación [ESCALAR:area]
            area_escalar = None
            if "[ESCALAR:" in respuesta:
                match = re.search(r'\[ESCALAR:(\w+)\]', respuesta)
                if match:
                    area_escalar = match.group(1)
                    respuesta = re.sub(r'\[ESCALAR:\w+\]', '', respuesta).strip()

            # Detectar [COPIA_ADMIN] — copia de cotización al dueño
            enviar_copia_admin = False
            if "[COPIA_ADMIN]" in respuesta:
                enviar_copia_admin = True
                respuesta = respuesta.replace("[COPIA_ADMIN]", "").strip()

            # Detectar [COMPROBANTE] — pedido confirmado + comprobante para el asesor
            # Formato: [COMPROBANTE]resumen del pedido[/COMPROBANTE]
            resumen_pedido = None
            match_comp = re.search(r'\[COMPROBANTE\](.*?)\[/COMPROBANTE\]', respuesta, re.DOTALL)
            if match_comp:
                resumen_pedido = match_comp.group(1).strip()
                respuesta = re.sub(r'\[COMPROBANTE\].*?\[/COMPROBANTE\]', '', respuesta, flags=re.DOTALL).strip()

            # Detectar comando de imagen [IMAGEN:nombre]
            imagen_nombre = None
            if "[IMAGEN:" in respuesta:
                match = re.search(r'\[IMAGEN:(\w+)\]', respuesta)
                if match:
                    imagen_nombre = match.group(1)
                    respuesta = re.sub(r'\[IMAGEN:\w+\]', '', respuesta).strip()

            # Detectar comando de sticker explícito [STICKER:nombre]
            sticker_nombre = None
            if "[STICKER:" in respuesta:
                match = re.search(r'\[STICKER:(\w+)\]', respuesta)
                if match:
                    sticker_nombre = match.group(1)
                    respuesta = re.sub(r'\[STICKER:\w+\]', '', respuesta).strip()

            # Auto-detectar despedida y asignar sticker automáticamente
            if not sticker_nombre and hasattr(proveedor, 'enviar_sticker'):
                FRASES_CIERRE = [
                    "aquí estaremos", "con gusto te cotizamos", "hasta luego",
                    "que tengas", "buen día", "buenas noches", "buenas tardes",
                    "nos vemos", "cuídate", "cualquier duda aquí", "estamos para",
                    "cuando gustes regresa", "con mucho gusto", "fue un placer",
                ]
                resp_lower = respuesta.lower()
                if any(frase in resp_lower for frase in FRASES_CIERRE):
                    sticker_nombre = "logo"

            # Enviar respuesta por WhatsApp
            await proveedor.enviar_mensaje(msg.telefono, respuesta)

            # Enviar imagen de catálogo si aplica
            if imagen_nombre and hasattr(proveedor, 'enviar_imagen'):
                await proveedor.enviar_imagen(msg.telefono, imagen_nombre)
                logger.info(f"Imagen enviada: {imagen_nombre}")

            # Enviar sticker si aplica
            if sticker_nombre and hasattr(proveedor, 'enviar_sticker'):
                await proveedor.enviar_sticker(msg.telefono, sticker_nombre)
                logger.info(f"Sticker enviado: {sticker_nombre}")

            # Sticker de bienvenida — primer mensaje del cliente
            elif es_primer_mensaje and hasattr(proveedor, 'enviar_sticker'):
                await proveedor.enviar_sticker(msg.telefono, "calidad")
                logger.info("Sticker de bienvenida enviado: calidad")

            logger.info(f"Respuesta a {msg.telefono}: {respuesta}")

            # Enviar copia de cotización al dueño (productos que no son lonas)
            if enviar_copia_admin and ADMIN_WHATSAPP:
                numero_limpio = msg.telefono.replace('@s.whatsapp.net', '')
                if numero_limpio.startswith("521") and len(numero_limpio) == 13:
                    area_tel = numero_limpio[3:6]
                    num_tel = numero_limpio[6:]
                    numero_display = f"+52 {area_tel} {num_tel[:3]} {num_tel[3:]}"
                else:
                    numero_display = f"+{numero_limpio}"
                msg_copia = (
                    f"📋 *COTIZACIÓN — CLIO*\n\n"
                    f"📱 *Cliente:* {numero_display}\n"
                    f"💬 *Pidió:* {msg.texto[:300]}\n\n"
                    f"📤 *Clio respondió:*\n{respuesta}"
                )
                try:
                    import httpx as _httpx
                    async with _httpx.AsyncClient(timeout=10.0) as _client:
                        await _client.post(
                            "https://gate.whapi.cloud/messages/text",
                            json={"to": ADMIN_WHATSAPP, "body": msg_copia},
                            headers={
                                "Authorization": f"Bearer {proveedor.token}",
                                "Content-Type": "application/json",
                            },
                        )
                    logger.info(f"Copia de cotización enviada al admin ({ADMIN_WHATSAPP})")
                except Exception as e:
                    logger.error(f"Error enviando copia al admin: {e}")

            # Pedido confirmado + comprobante → reenviar al asesor (Anna)
            if resumen_pedido and ASESOR_WHATSAPP:
                numero_limpio = msg.telefono.replace('@s.whatsapp.net', '')
                if numero_limpio.startswith("521") and len(numero_limpio) == 13:
                    num_display = f"+52 {numero_limpio[3:6]} {numero_limpio[6:9]} {numero_limpio[9:]}"
                else:
                    num_display = f"+{numero_limpio}"
                nombre_cli = msg.nombre_perfil or "Cliente"
                msg_asesor = (
                    f"✅ *PEDIDO CONFIRMADO — CLIO*\n\n"
                    f"👤 *Cliente:* {nombre_cli}\n"
                    f"📱 *WhatsApp:* {num_display}\n\n"
                    f"{resumen_pedido}\n\n"
                    f"👉 Contactar: wa.me/{numero_limpio}"
                )
                try:
                    await proveedor.enviar_mensaje(ASESOR_WHATSAPP, msg_asesor)
                    logger.info(f"Pedido confirmado enviado al asesor ({ASESOR_WHATSAPP})")
                    # Si el cliente mandó comprobante (imagen/documento) en este turno, reenviarlo
                    if media_original and tipo_original == "image" and hasattr(proveedor, 'enviar_imagen_url'):
                        await proveedor.enviar_imagen_url(ASESOR_WHATSAPP, media_original, caption=f"🧾 Comprobante de pago — {nombre_cli}")
                        logger.info("Comprobante (imagen) reenviado al asesor")
                    elif media_original and tipo_original == "document" and hasattr(proveedor, 'enviar_documento_url'):
                        await proveedor.enviar_documento_url(ASESOR_WHATSAPP, media_original, caption=f"🧾 Comprobante de pago — {nombre_cli}")
                        logger.info("Comprobante (documento) reenviado al asesor")
                except Exception as e:
                    logger.error(f"Error enviando pedido al asesor: {e}")

            # Enviar alerta al asesor si hay escalación
            if area_escalar:
                # Construir resumen limpio del historial (sin duplicados ni texto de imágenes)
                def _limpiar_contenido(texto: str) -> str:
                    """Simplifica el texto de imágenes y recorta."""
                    if "[El cliente envió una imagen" in texto:
                        return "[imagen]"
                    if "[ESCALAR:" in texto or "[IMAGEN:" in texto or "[STICKER:" in texto:
                        import re as _re
                        texto = _re.sub(r'\[\w+:[^\]]*\]', '', texto).strip()
                    return texto[:120]

                resumen_lineas = []
                mensajes_vistos = set()
                for h in historial[-8:]:
                    contenido = _limpiar_contenido(h['content'])
                    if not contenido or contenido in mensajes_vistos:
                        continue
                    mensajes_vistos.add(contenido)
                    rol = "👤 Cliente" if h["role"] == "user" else "🤖 Clio"
                    resumen_lineas.append(f"{rol}: {contenido}")

                # Agregar el mensaje actual solo si no es una imagen ya registrada
                texto_actual = _limpiar_contenido(msg.texto)
                if texto_actual and texto_actual not in mensajes_vistos:
                    resumen_lineas.append(f"👤 Cliente: {texto_actual}")

                resumen_completo = "\n".join(resumen_lineas) if resumen_lineas else "(sin historial)"

                await enviar_alerta_asesor(
                    area=area_escalar,
                    telefono_cliente=msg.telefono,
                    resumen=resumen_completo,
                    whapi_token=proveedor.token,
                )
                logger.info(f"Escalación enviada a área: {area_escalar}")

                # Enviar foto del asesor al cliente según el área
                # Solo si Clio no la envió ya via [IMAGEN:] para evitar duplicado
                if hasattr(proveedor, 'enviar_imagen') and not imagen_nombre:
                    foto_asesor = {
                        "asesor":         "asesor_ana",
                        "director":       "asesor_ana",
                        "letreros":       "asesor_brayan",
                        "administracion": "asesor_tere",
                    }.get(area_escalar)
                    if foto_asesor:
                        await proveedor.enviar_imagen(msg.telefono, foto_asesor)
                        logger.info(f"Foto de asesor enviada al cliente: {foto_asesor}")

        return {"status": "ok"}

    except Exception as e:
        logger.error(f"Error en webhook: {e}")
        raise HTTPException(status_code=500, detail=str(e))
