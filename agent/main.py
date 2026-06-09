# agent/main.py — Servidor FastAPI + Webhook de WhatsApp
# Generado por AgentKit para LiTek

import os
import asyncio
import logging
from datetime import datetime, timedelta
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

# Archivo de diseño/arte que el cliente mandó para imprimir (telefono → media_url).
# Se guarda cuando manda el diseño y se reenvía al asesor cuando confirma el pago.
_archivo_diseno: dict[str, str] = {}

# Clientes que YA pagaron pero NO han mandado su archivo de impresión (telefono → nombre).
# Cuando el archivo llegue después, se reenvía al asesor de inmediato.
_pago_sin_archivo: dict[str, str] = {}

# Pedidos ya confirmados al grupo (telefono → datetime), para no reenviar el mismo
# pedido varias veces cuando el cliente manda detalles del pago en mensajes seguidos.
_pedido_confirmado_ts: dict[str, datetime] = {}
VENTANA_PEDIDO_MIN = 60  # no reenviar el pedido confirmado del mismo cliente en 60 min

# Última imagen/documento que mandó el cliente (telefono → "tipo|url").
# Red de seguridad: si Clio confirma el pago en un turno sin imagen, usamos esta
# como comprobante (el cliente la mandó un mensaje antes).
_ultima_imagen: dict[str, str] = {}

from agent.brain import generar_respuesta
from agent.memory import (
    inicializar_db, guardar_mensaje, obtener_historial, registrar_ruleta, verificar_ruleta,
    registrar_crm, registrar_o_actualizar_crm, listar_crm, actualizar_crm, verificar_usuario_crm,
    minutos_desde_ultimo_mensaje, asignar_ruletas_sin_avanzar,
    carga_por_asesor, consolidar_duplicados_crm, obtener_conversacion_crm,
    tomar_control, devolver_clio, estado_control,
)

# ── Reparto POR PRODUCTO (no por turnos) ──────────────────────────────────────
# El primer producto que identifica el cliente define su asesor; luego no cambia.
PRODUCTO_A_ASESOR = {
    # Anna — impresión general
    "lona": "Anna", "vinil_impreso": "Anna", "vinil_pvc": "Anna",
    "coroplast": "Anna", "microperforado": "Anna", "papel_couche": "Anna",
    "papel_bond": "Anna", "tabloide_laser": "Anna", "corte_vinil": "Anna",
    # Brayan — letreros, rótulos y etiquetas
    "etiqueta_5x5": "Brayan", "etiqueta_personalizada": "Brayan",
}


def asesor_por_producto(producto: str) -> str:
    """Asesor según el producto. Por defecto Anna (impresión general)."""
    return PRODUCTO_A_ASESOR.get(producto, "Anna")
from agent.providers import obtener_proveedor
from agent.transcription import transcribir_audio
from agent.document_reader import leer_documento
from agent.escalation import enviar_alerta_asesor, AREAS
from agent.reporte_diario import generar_reporte_diario
from agent.seguimiento import enviar_seguimientos, cargar_cache_desde_db
from agent.crm import HTML_PANEL, crear_token, verificar_token

# Número del dueño para recibir copia de cotizaciones de productos que no son lonas
ADMIN_WHATSAPP = os.getenv("ADMIN_WHATSAPP", "529812710000")
# Grupo de alertas de Clio (equipo LiTek) — recibe pedidos confirmados, comprobantes y archivos
ASESOR_WHATSAPP = os.getenv("ASESOR_WHATSAPP", "120363425558631008@g.us")

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
    # Seguimiento automático: 1 mensaje a clientes inactivos por más de 1 hora.
    # Seguro con PostgreSQL persistente: el marcador "ya envié" sobrevive reinicios.
    scheduler.add_job(
        enviar_seguimientos,
        "interval",
        minutes=30,
        args=[proveedor.token],
        id="seguimiento_inactivos",
        name="Seguimiento clientes inactivos cada 30 minutos",
        replace_existing=True,
    )
    # Reparto de ganadores de ruleta que llevan +2h sin avanzar (sin dueño).
    # Si compraron antes, ya tienen dueño y se ignoran (no se duplica).
    scheduler.add_job(
        asignar_ruletas_sin_avanzar,
        "interval",
        minutes=30,
        id="reparto_ruleta",
        name="Repartir ganadores de ruleta sin avanzar (+2h) entre Anna y Brayan",
        replace_existing=True,
    )
    scheduler.start()
    logger.info("Scheduler iniciado — reporte 5AM + seguimiento 30min + reparto ruleta 30min (Campeche)")

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


@app.get("/diag/clio")
async def diag_clio():
    """Diagnóstico temporal: prueba generar_respuesta y reporta el error real."""
    import traceback
    try:
        señales = {}
        resp = await generar_respuesta("hola, ¿cuánto una lona de 1x1?", [], señales=señales)
        return {"ok": True, "respuesta": resp[:300], "señales": señales}
    except Exception as e:
        return {"ok": False, "error": str(e), "traceback": traceback.format_exc()[-1500:]}


@app.get("/")
async def health_check():
    """Endpoint de salud para Railway/monitoreo."""
    return {"status": "ok", "service": "agentkit-litek", "agente": "Clio"}


# ─────────────────────────────────────────────────────────────────────────────
# CRM — panel de seguimiento del equipo
# ─────────────────────────────────────────────────────────────────────────────
from fastapi.responses import HTMLResponse


def _crm_usuario_de_request(request: Request) -> dict | None:
    """Extrae y valida el token del header Authorization."""
    auth = request.headers.get("Authorization", "")
    if auth.startswith("Bearer "):
        return verificar_token(auth[7:])
    return None


# Quién ve qué: director (Chino) y administradora (Tere) ven TODO;
# cada asesor (Anna, Brayan) ve SOLO lo asignado a él.
CRM_VEN_TODO = {"chino", "tere"}
CRM_USUARIO_A_ASESOR = {"anna": "Anna", "brayan": "Brayan"}


def _asesor_filtro_de(usuario: str) -> str:
    """Asesor por el que se filtra. '' = ve todo (director/admin)."""
    if usuario in CRM_VEN_TODO:
        return ""
    return CRM_USUARIO_A_ASESOR.get(usuario, usuario.capitalize())


@app.get("/crm")
async def crm_panel():
    """Página del panel CRM (login + tablero)."""
    return HTMLResponse(HTML_PANEL)


@app.post("/crm/login")
async def crm_login(request: Request):
    """Autenticación del CRM. Retorna token de sesión."""
    data = await request.json()
    usuario = data.get("usuario", "")
    password = data.get("password", "")
    u = await verificar_usuario_crm(usuario, password)
    if u:
        return {"ok": True, "token": crear_token(u["usuario"], u["nombre"]), "nombre": u["nombre"]}
    return {"ok": False}


@app.get("/crm/api/registros")
async def crm_registros(request: Request, estado: str = "", tipo: str = ""):
    """Lista registros del CRM (requiere token). Cada asesor ve solo lo suyo."""
    u = _crm_usuario_de_request(request)
    if not u:
        raise HTTPException(status_code=401, detail="No autorizado")
    # Filtro por persona: director/admin ven todo, cada asesor solo lo asignado a él
    asesor_filtro = _asesor_filtro_de(u["usuario"])
    ve_todo = u["usuario"] in CRM_VEN_TODO
    registros = await listar_crm(estado=estado, tipo=tipo, asesor=asesor_filtro)
    # Stats por estado (respetando el filtro de persona, sin filtro de estado)
    todos = await listar_crm(tipo=tipo, asesor=asesor_filtro, limite=1000)
    stats = {"nuevo": 0, "asignado": 0, "proceso": 0, "cerrado": 0}
    for r in todos:
        stats[r["estado"]] = stats.get(r["estado"], 0) + 1
    # Carga por asesor (solo para quien ve todo): Anna 10, Brayan 15, Tere 3...
    carga = await carga_por_asesor() if ve_todo else None
    return {
        "registros": registros,
        "stats": stats,
        "nombre": u["nombre"],
        "es_director": ve_todo,
        "carga": carga,
    }


@app.get("/crm/api/chat")
async def crm_chat(request: Request, telefono: str = ""):
    """Devuelve la conversación de un cliente + estado de control (requiere token)."""
    u = _crm_usuario_de_request(request)
    if not u:
        raise HTTPException(status_code=401, detail="No autorizado")
    mensajes = await obtener_conversacion_crm(telefono)
    control = await estado_control(telefono)
    return {"mensajes": mensajes, "control": control}


@app.post("/crm/api/control")
async def crm_control(request: Request):
    """Toma o devuelve el control de una conversación (requiere token)."""
    u = _crm_usuario_de_request(request)
    if not u:
        raise HTTPException(status_code=401, detail="No autorizado")
    data = await request.json()
    telefono = data.get("telefono", "")
    accion = data.get("accion", "")  # "tomar" | "devolver"
    if accion == "tomar":
        await tomar_control(telefono, u["nombre"])
    elif accion == "devolver":
        await devolver_clio(telefono)
    return {"ok": True, "control": await estado_control(telefono)}


def _wa_destino(telefono: str) -> str:
    """Normaliza a formato WhatsApp México: 521 + últimos 10 dígitos."""
    d = "".join(c for c in (telefono or "") if c.isdigit())
    return ("521" + d[-10:]) if len(d) >= 10 else d


@app.post("/crm/api/enviar")
async def crm_enviar(request: Request):
    """Envía un mensaje al cliente desde el número de Clio (requiere token)."""
    u = _crm_usuario_de_request(request)
    if not u:
        raise HTTPException(status_code=401, detail="No autorizado")
    data = await request.json()
    telefono = data.get("telefono", "")
    mensaje = (data.get("mensaje", "") or "").strip()
    if not telefono or not mensaje:
        return {"ok": False, "error": "Falta teléfono o mensaje"}
    destino = _wa_destino(telefono)  # 521 + 10 dígitos (si no, WhatsApp no entrega)
    # Al responder, el asesor toma el control automáticamente (refresca el timer)
    await tomar_control(destino, u["nombre"])
    ok = await proveedor.enviar_mensaje(destino, mensaje)
    # Guardar en el historial marcado como asesor (para distinguirlo de Clio)
    await guardar_mensaje(destino, "assistant", f"[Asesor {u['nombre']}] {mensaje}")
    if not ok:
        logger.error(f"Envío desde panel falló a {destino}")
    return {"ok": ok}


@app.post("/crm/api/consolidar")
async def crm_consolidar(request: Request):
    """Une tarjetas duplicadas del mismo cliente (solo director/admin)."""
    u = _crm_usuario_de_request(request)
    if not u or u["usuario"] not in CRM_VEN_TODO:
        raise HTTPException(status_code=401, detail="No autorizado")
    resultado = await consolidar_duplicados_crm()
    return {"ok": True, **resultado}


@app.post("/crm/api/registro/{registro_id}")
async def crm_actualizar(registro_id: int, request: Request):
    """Actualiza estado, asesor o notas de un registro (requiere token)."""
    u = _crm_usuario_de_request(request)
    if not u:
        raise HTTPException(status_code=401, detail="No autorizado")
    data = await request.json()
    ok = await actualizar_crm(
        registro_id,
        estado=data.get("estado"),
        asesor=data.get("asesor"),
        notas=data.get("notas"),
    )
    return {"ok": ok}


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

    # Registrar en el CRM SIN dueño todavía. Si el cliente avanza (cotiza/compra),
    # se le asigna por el flujo normal. Si NO avanza en 2h, un job lo reparte por
    # turnos (ver asignar_ruletas_sin_avanzar). Así no se duplica si compra rápido.
    try:
        await registrar_o_actualizar_crm(
            telefono=telefono,
            nombre=nombre,
            descripcion=f"🎁 Ganó en ruleta: {premio}. {descripcion}",
            tipo="ruleta",
            estado_minimo="nuevo",
        )
    except Exception as e:
        logger.error(f"Error registrando ruleta en CRM: {e}")

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


async def _reenviar_media(destino: str, tipo: str, media_url: str, media_id: str, caption: str = "") -> bool:
    """
    Reenvía una imagen/documento a otro número. Compatible con ambos proveedores:
    Meta usa media_id (preferido, no expira); Whapi usa media_url directa.
    """
    if tipo == "image":
        if media_id and hasattr(proveedor, "enviar_imagen_id"):
            return await proveedor.enviar_imagen_id(destino, media_id, caption=caption)
        if media_url and hasattr(proveedor, "enviar_imagen_url"):
            return await proveedor.enviar_imagen_url(destino, media_url, caption=caption)
    elif tipo == "document":
        if media_id and hasattr(proveedor, "enviar_documento_id"):
            return await proveedor.enviar_documento_id(destino, media_id, caption=caption)
        if media_url and hasattr(proveedor, "enviar_documento_url"):
            return await proveedor.enviar_documento_url(destino, media_url, caption=caption)
    return False


async def _procesar_mensaje(msg):
    """Procesa un mensaje en segundo plano (responder rápido evita reintentos de Whapi)."""
    try:
        # Guardar media y tipo ORIGINALES antes de modificarlos (para reenviar comprobantes)
        media_original = msg.media_url
        tipo_original = msg.tipo
        media_id_original = getattr(msg, "media_id", "")  # vacío en Whapi, lleno en Meta

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
            return

        logger.info(f"Mensaje de {msg.telefono} [{msg.tipo}]: {msg.texto}")

        # Recordar la última imagen/documento del cliente (red de seguridad para comprobantes)
        if media_original and tipo_original in ("image", "document"):
            _ultima_imagen[msg.telefono] = f"{tipo_original}|{media_original}|{media_id_original}"

        # Obtener historial ANTES de guardar el mensaje actual
        historial = await obtener_historial(msg.telefono)
        es_primer_mensaje = len(historial) == 0

        # Registrar en el CRM SIN dueño todavía (un "hola" no se reparte):
        #  • Cliente NUEVO → en su primer mensaje
        #  • Cliente RECURRENTE → si regresa tras +6h de silencio
        # El reparto por turnos se hace MÁS ABAJO, solo cuando Clio da un precio
        # (producto identificado = interés real). Así no se reparten saludos vacíos.
        SILENCIO_RECURRENTE_MIN = 360  # 6 horas
        tel_limpio = msg.telefono.replace("@s.whatsapp.net", "")
        try:
            if es_primer_mensaje:
                await registrar_o_actualizar_crm(
                    telefono=tel_limpio,
                    nombre=msg.nombre_perfil or "Cliente nuevo",
                    descripcion=f"🆕 Cliente nuevo. Primer mensaje: {msg.texto[:200]}",
                    tipo="cliente",
                    estado_minimo="nuevo",
                )
            else:
                mins = await minutos_desde_ultimo_mensaje(msg.telefono)
                if mins is not None and mins >= SILENCIO_RECURRENTE_MIN:
                    horas = int(mins // 60)
                    await registrar_o_actualizar_crm(
                        telefono=tel_limpio,
                        nombre=msg.nombre_perfil or "Cliente",
                        descripcion=f"🔄 Cliente regresó (tras {horas}h sin escribir): {msg.texto[:200]}",
                        tipo="cliente",
                        estado_minimo="nuevo",
                    )
        except Exception as e:
            logger.error(f"Error registrando cliente en CRM: {e}")

        # ── BANDEJA: si un asesor tomó el control, Clio NO responde ──────────
        # Guardamos el mensaje del cliente (para que el asesor lo vea) y salimos.
        if await esta_en_modo_humano(msg.telefono):
            await guardar_mensaje(msg.telefono, "user", msg.texto)
            logger.info(f"Modo humano activo para {msg.telefono} — Clio no responde")
            return

        # Generar respuesta con Claude (con soporte de imagen si aplica).
        # `señales` nos dice si Clio cotizó en este turno (producto identificado).
        señales: dict = {}
        respuesta = await generar_respuesta(
            msg.texto,
            historial,
            media_url=msg.media_url,
            whapi_token=proveedor.token,
            tipo=msg.tipo,
            nombre_perfil=msg.nombre_perfil,
            señales=señales,
        )

        # Si Clio dio un precio → producto identificado → repartir por turnos
        # (Anna/Brayan). Solo asigna dueño si la tarjeta aún no tiene uno.
        if señales.get("cotizo"):
            try:
                producto = señales.get("producto", "")
                asesor_prod = asesor_por_producto(producto)
                await registrar_o_actualizar_crm(
                    telefono=tel_limpio,
                    nombre=msg.nombre_perfil or "Cliente",
                    descripcion=f"💲 Cotización ({producto or 'producto'}): {msg.texto[:140]}",
                    tipo="cliente",
                    estado_minimo="asignado",  # cotizó → ya tiene dueño → pasa a Asignados
                    asesor_si_nuevo=asesor_prod,  # dueño fijo según el producto
                )
            except Exception as e:
                logger.error(f"Error asignando lead cotizado en CRM: {e}")

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

        # Detectar [MOTIVO] — resumen conciso del motivo de escalación (lo genera Clio)
        motivo_escalacion = None
        match_motivo = re.search(r'\[MOTIVO\](.*?)\[/MOTIVO\]', respuesta, re.DOTALL)
        if match_motivo:
            motivo_escalacion = match_motivo.group(1).strip()
            respuesta = re.sub(r'\[MOTIVO\].*?\[/MOTIVO\]', '', respuesta, flags=re.DOTALL).strip()

        # Detectar [COPIA_ADMIN] — copia de cotización al dueño
        enviar_copia_admin = False
        if "[COPIA_ADMIN]" in respuesta:
            enviar_copia_admin = True
            respuesta = respuesta.replace("[COPIA_ADMIN]", "").strip()

        # Si este turno es de PAGO (tiene comprobante), NO tratar el archivo como diseño
        hay_comprobante_en_turno = "[COMPROBANTE]" in respuesta

        # Detectar [GUARDAR_ARCHIVO] — el cliente mandó su diseño/arte para imprimir
        # (solo si NO es un turno de pago — un comprobante no es un diseño)
        if "[GUARDAR_ARCHIVO]" in respuesta:
            respuesta = respuesta.replace("[GUARDAR_ARCHIVO]", "").strip()
            if media_original and tipo_original in ("image", "document") and not hay_comprobante_en_turno:
                # ¿El cliente ya pagó y solo faltaba el archivo? → reenviar al asesor YA
                if msg.telefono in _pago_sin_archivo:
                    nombre_cli = _pago_sin_archivo.pop(msg.telefono) or (msg.nombre_perfil or "Cliente")
                    cap = f"🎨 Archivo para imprimir (el que faltaba) — {nombre_cli}"
                    try:
                        await _reenviar_media(ASESOR_WHATSAPP, tipo_original, media_original, media_id_original, caption=cap)
                        await proveedor.enviar_mensaje(
                            ASESOR_WHATSAPP,
                            f"✅ {nombre_cli} ya mandó su archivo para imprimir. Pedido completo, listo para producción."
                        )
                        logger.info(f"Archivo tardío reenviado al asesor para {msg.telefono}")
                    except Exception as e:
                        logger.error(f"Error reenviando archivo tardío: {e}")
                else:
                    # Aún no paga — guardar para reenviar cuando confirme el pago
                    _archivo_diseno[msg.telefono] = f"{tipo_original}|{media_original}|{media_id_original}"
                    logger.info(f"Archivo de diseño guardado para {msg.telefono}")

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

        # Dedup: si ya se confirmó un pedido de este cliente hace poco, no reenviar
        # (Clio a veces genera [COMPROBANTE] en varios mensajes seguidos)
        if resumen_pedido and ASESOR_WHATSAPP:
            ultimo = _pedido_confirmado_ts.get(msg.telefono)
            if ultimo and (datetime.utcnow() - ultimo) < timedelta(minutes=VENTANA_PEDIDO_MIN):
                logger.info(f"Pedido confirmado duplicado ignorado para {msg.telefono}")
                resumen_pedido = None
            else:
                _pedido_confirmado_ts[msg.telefono] = datetime.utcnow()

        # Pedido confirmado + comprobante → reenviar al asesor (Anna)
        if resumen_pedido and ASESOR_WHATSAPP:
            numero_limpio = msg.telefono.replace('@s.whatsapp.net', '')
            if numero_limpio.startswith("521") and len(numero_limpio) == 13:
                num_display = f"+52 {numero_limpio[3:6]} {numero_limpio[6:9]} {numero_limpio[9:]}"
            else:
                num_display = f"+{numero_limpio}"
            nombre_cli = msg.nombre_perfil or "Cliente"
            # Registrar el pedido en el CRM → la tarjeta del cliente pasa a "En proceso".
            # Si la tarjeta ya tenía dueño (por el producto que cotizó/escaló), lo conserva.
            # Si llegó directo a pedido sin dueño, default Anna (impresión general).
            try:
                await registrar_o_actualizar_crm(
                    telefono=numero_limpio,
                    nombre=nombre_cli,
                    descripcion=resumen_pedido,
                    tipo="pedido",
                    estado_minimo="proceso",
                    asesor_si_nuevo="Anna",
                )
            except Exception as e:
                logger.error(f"Error registrando pedido en CRM: {e}")
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

                # 1) Reenviar el COMPROBANTE de pago.
                # Si el turno actual trae imagen/doc, ese es el comprobante.
                # Si no (Clio confirmó el pago en un turno de texto), usar la última
                # imagen que mandó el cliente (la mandó un mensaje antes).
                comp_tipo, comp_url, comp_id = "", "", ""
                if media_original and tipo_original in ("image", "document"):
                    comp_tipo, comp_url, comp_id = tipo_original, media_original, media_id_original
                else:
                    ult = _ultima_imagen.get(msg.telefono, "")
                    if ult:
                        partes = ult.split("|", 2)
                        comp_tipo = partes[0] if len(partes) > 0 else ""
                        comp_url = partes[1] if len(partes) > 1 else ""
                        comp_id = partes[2] if len(partes) > 2 else ""

                if (comp_url or comp_id) and comp_tipo in ("image", "document"):
                    ok_comp = await _reenviar_media(ASESOR_WHATSAPP, comp_tipo, comp_url, comp_id, caption=f"🧾 Comprobante de pago — {nombre_cli}")
                    logger.info(f"Comprobante reenviado al asesor: {ok_comp}")
                else:
                    logger.warning(f"No se encontró comprobante para {msg.telefono}")

                # 2) Reenviar el ARCHIVO DE DISEÑO para imprimir (guardado de un turno anterior)
                archivo_guardado = _archivo_diseno.pop(msg.telefono, None)
                partes_a = (archivo_guardado or "").split("|", 2)
                tipo_arch = partes_a[0] if len(partes_a) > 0 else ""
                url_arch = partes_a[1] if len(partes_a) > 1 else ""
                id_arch = partes_a[2] if len(partes_a) > 2 else ""
                # Red de seguridad: si el "diseño" guardado es el MISMO archivo que el
                # comprobante, es un comprobante mal etiquetado — no reenviar duplicado
                if archivo_guardado and ((url_arch and url_arch == comp_url) or (id_arch and id_arch == comp_id)):
                    logger.info("Archivo guardado == comprobante — se omite (evita duplicado)")
                    archivo_guardado = None
                if archivo_guardado:
                    ok_arch = await _reenviar_media(ASESOR_WHATSAPP, tipo_arch, url_arch, id_arch, caption=f"🎨 Archivo para imprimir — {nombre_cli}")
                    logger.info(f"Archivo de diseño reenviado al asesor: {ok_arch}")
                else:
                    # El cliente pagó pero NO ha mandado su archivo de impresión
                    _pago_sin_archivo[msg.telefono] = nombre_cli
                    await proveedor.enviar_mensaje(
                        ASESOR_WHATSAPP,
                        f"⚠️ *{nombre_cli} YA PAGÓ pero falta su archivo para imprimir.*\n"
                        f"Clio se lo está pidiendo. Si no lo manda pronto, contáctalo: wa.me/{numero_limpio}"
                    )
                    logger.info(f"Pago sin archivo — avisado al asesor, pendiente {msg.telefono}")

                # Limpiar la última imagen ya usada como comprobante
                _ultima_imagen.pop(msg.telefono, None)
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

            # Usar el motivo conciso de Clio si existe; si no, el historial resumido
            if motivo_escalacion:
                resumen_completo = motivo_escalacion
            else:
                resumen_completo = "\n".join(resumen_lineas) if resumen_lineas else "(sin historial)"

            await enviar_alerta_asesor(
                area=area_escalar,
                telefono_cliente=msg.telefono,
                resumen=resumen_completo,
                whapi_token=proveedor.token,
                nombre_cliente=msg.nombre_perfil,
            )
            logger.info(f"Escalación enviada a área: {area_escalar}")

            # Registrar la escalación en el CRM, asignada al asesor correspondiente
            _asesor_area = {
                "asesor": "Anna", "director": "Chino",
                "letreros": "Brayan", "administracion": "Tere",
            }.get(area_escalar, "")
            try:
                await registrar_o_actualizar_crm(
                    telefono=msg.telefono.replace("@s.whatsapp.net", ""),
                    nombre=msg.nombre_perfil or "Cliente",
                    descripcion=resumen_completo,
                    tipo="escalacion",
                    estado_minimo="asignado",
                    asesor=_asesor_area,
                )
            except Exception as e:
                logger.error(f"Error registrando escalación en CRM: {e}")

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

    except Exception as e:
        logger.error(f"Error procesando mensaje en background: {e}")


@app.post("/webhook")
async def webhook_handler(request: Request):
    """
    Recibe mensajes de WhatsApp. Responde 200 de inmediato y procesa en
    segundo plano, para que Whapi no reintente (evita alertas duplicadas).
    """
    try:
        mensajes = await proveedor.parsear_webhook(request)
    except Exception as e:
        logger.error(f"Error parseando webhook: {e}")
        return {"status": "ok"}

    for msg in mensajes:
        if msg.es_propio:
            continue
        if msg.mensaje_id and msg.mensaje_id in _ids_procesados:
            logger.info(f"Mensaje duplicado ignorado: {msg.mensaje_id}")
            continue
        if msg.mensaje_id:
            _ids_procesados[msg.mensaje_id] = True
            if len(_ids_procesados) > MAX_IDS_CACHE:
                _ids_procesados.popitem(last=False)
        asyncio.create_task(_procesar_mensaje(msg))

    return {"status": "ok"}
