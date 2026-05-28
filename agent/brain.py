# agent/brain.py — Cerebro del agente: conexión con Claude API + tool_use
# Generado por AgentKit para LiTek

import os
import json
import base64
import yaml
import logging
import httpx
from anthropic import AsyncAnthropic
from dotenv import load_dotenv

from agent.tools import calcular_precio

load_dotenv()
logger = logging.getLogger("agentkit")

client = AsyncAnthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))

# ─────────────────────────────────────────────────────────────────────────────
# Definición de herramientas para Claude (tool_use)
# ─────────────────────────────────────────────────────────────────────────────
HERRAMIENTAS_CLAUDE = [
    {
        "name": "calcular_precio",
        "description": (
            "Calcula el precio exacto de cualquier producto de LiTek. "
            "DEBES llamar a esta herramienta SIEMPRE que un cliente pida precio o cotización. "
            "NUNCA calcules precios mentalmente — solo esta herramienta garantiza exactitud. "
            "Si el resultado tiene 'accion': 'escalar_asesor', escala al asesor correspondiente."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "producto": {
                    "type": "string",
                    "enum": [
                        "lona", "vinil_impreso", "vinil_pvc", "coroplast",
                        "microperforado", "papel_couche", "papel_bond",
                        "etiqueta_5x5", "etiqueta_personalizada",
                        "tabloide_laser", "corte_vinil",
                    ],
                    "description": (
                        "Tipo de producto: "
                        "lona=lona gran formato, "
                        "vinil_impreso=vinil impreso gran formato, "
                        "vinil_pvc=vinil sobre PVC 3mm, "
                        "coroplast=vinil sobre coroplast, "
                        "microperforado=vinil microperforado, "
                        "papel_couche=papel couché gran formato, "
                        "papel_bond=papel bond gran formato, "
                        "etiqueta_5x5=etiquetas vinil 5×5cm, "
                        "etiqueta_personalizada=etiquetas personalizadas con suaje, "
                        "tabloide_laser=tabloide láser, "
                        "corte_vinil=corte de vinil"
                    ),
                },
                "base_cm": {
                    "type": "number",
                    "description": (
                        "Ancho/base del producto en centímetros. "
                        "Para corte_vinil: longitud total en cm (ej. 150 = 1.5 metros lineales)."
                    ),
                },
                "alto_cm": {
                    "type": "number",
                    "description": (
                        "Alto del producto en centímetros. "
                        "Para corte_vinil y etiqueta_5x5: se ignora, pon 0."
                    ),
                },
                "cantidad": {
                    "type": "integer",
                    "description": "Número de piezas (default: 1).",
                },
                "expres": {
                    "type": "boolean",
                    "description": "True si el cliente quiere servicio exprés (+35% al precio).",
                },
            },
            "required": ["producto", "base_cm", "alto_cm"],
        },
    }
]


def cargar_config_prompts() -> dict:
    """Lee toda la configuración desde config/prompts.yaml."""
    try:
        with open("config/prompts.yaml", "r", encoding="utf-8") as f:
            return yaml.safe_load(f) or {}
    except FileNotFoundError:
        logger.error("config/prompts.yaml no encontrado")
        return {}


def cargar_system_prompt() -> str:
    config = cargar_config_prompts()
    return config.get("system_prompt", "Eres Clio, asistente de ventas de LiTek. Responde en español.")


def obtener_mensaje_error() -> str:
    config = cargar_config_prompts()
    return config.get("error_message", "Ops, tuve un problema técnico. ¿Puedes intentarlo en un momento?")


def obtener_mensaje_fallback() -> str:
    config = cargar_config_prompts()
    return config.get("fallback_message", "Mmm, no entendí bien. ¿Me puedes decir qué producto necesitas y las medidas?")


async def descargar_imagen(media_url: str, whapi_token: str) -> tuple[str, str] | None:
    """Descarga imagen de Whapi y la convierte a base64. Retorna (b64, media_type) o None."""
    if not media_url:
        return None
    try:
        headers = {"Authorization": f"Bearer {whapi_token}"}
        logger.info(f"Descargando imagen: {media_url}")
        async with httpx.AsyncClient(timeout=30.0, follow_redirects=True) as c:
            r = await c.get(media_url, headers=headers)
            logger.info(f"Imagen: HTTP {r.status_code} | {r.headers.get('content-type','?')} | {len(r.content)} bytes")
            if r.status_code != 200:
                logger.error(f"Error descargando imagen: {r.status_code}")
                return None
            content_type = r.headers.get("content-type", "image/jpeg")
            media_type   = content_type.split(";")[0].strip()
            if media_type not in ("image/jpeg", "image/png", "image/gif", "image/webp"):
                media_type = "image/jpeg"
            return base64.standard_b64encode(r.content).decode("utf-8"), media_type
    except Exception as e:
        logger.error(f"Excepción descargando imagen: {e}")
        return None


async def generar_respuesta(
    mensaje: str,
    historial: list[dict],
    media_url: str = "",
    whapi_token: str = "",
    tipo: str = "text",
) -> str:
    """
    Genera una respuesta usando Claude API con soporte de tool_use.

    El modelo llama automáticamente a calcular_precio() cuando necesita
    cotizar un producto — garantizando exactitud matemática en precios.
    """
    if not mensaje or len(mensaje.strip()) < 2:
        return obtener_mensaje_fallback()

    system_prompt = cargar_system_prompt()

    # Construir historial de mensajes
    mensajes: list[dict] = []
    for msg in historial:
        mensajes.append({"role": msg["role"], "content": msg["content"]})

    # Mensaje actual del usuario (con imagen si aplica)
    if tipo == "image" and media_url and whapi_token:
        imagen = await descargar_imagen(media_url, whapi_token)
        if imagen:
            b64, media_type = imagen
            contenido_usuario = [
                {
                    "type": "image",
                    "source": {"type": "base64", "media_type": media_type, "data": b64},
                },
                {
                    "type": "text",
                    "text": mensaje or "¿Qué ves en esta imagen? Ayúdame a cotizarla si es un diseño para imprimir.",
                },
            ]
        else:
            contenido_usuario = mensaje
    else:
        contenido_usuario = mensaje

    mensajes.append({"role": "user", "content": contenido_usuario})

    # ── Loop de tool_use ─────────────────────────────────────────────────────
    MAX_ITER = 6  # máximo de rondas tool_use antes de abortar
    try:
        for _ in range(MAX_ITER):
            response = await client.messages.create(
                model="claude-sonnet-4-6",
                max_tokens=1024,
                system=system_prompt,
                messages=mensajes,
                tools=HERRAMIENTAS_CLAUDE,
            )

            # ── Respuesta final ──────────────────────────────────────────────
            if response.stop_reason != "tool_use":
                respuesta = next(
                    (b.text for b in response.content if hasattr(b, "text")),
                    obtener_mensaje_fallback(),
                )
                logger.info(
                    f"Respuesta generada "
                    f"({response.usage.input_tokens} in / {response.usage.output_tokens} out)"
                )
                return respuesta

            # ── El modelo quiere usar una herramienta ────────────────────────
            # 1. Añadir respuesta del asistente (con el tool_use block) al historial
            mensajes.append({"role": "assistant", "content": response.content})

            # 2. Ejecutar cada herramienta solicitada
            tool_results = []
            for block in response.content:
                if block.type != "tool_use":
                    continue

                logger.info(f"Tool call → {block.name}({block.input})")
                try:
                    if block.name == "calcular_precio":
                        resultado = calcular_precio(**block.input)
                    else:
                        resultado = {"accion": "error", "mensaje": f"Herramienta desconocida: {block.name}"}

                    tool_results.append({
                        "type":        "tool_result",
                        "tool_use_id": block.id,
                        "content":     json.dumps(resultado, ensure_ascii=False),
                    })
                    logger.info(f"Tool result: {resultado}")

                except Exception as e:
                    logger.error(f"Error ejecutando tool {block.name}: {e}")
                    tool_results.append({
                        "type":        "tool_result",
                        "tool_use_id": block.id,
                        "is_error":    True,
                        "content":     str(e),
                    })

            # 3. Devolver resultados al modelo
            mensajes.append({"role": "user", "content": tool_results})

        # Si llegamos aquí se agotaron las iteraciones
        logger.error("Se agotaron las iteraciones del loop tool_use")
        return obtener_mensaje_error()

    except Exception as e:
        logger.error(f"Error Claude API: {e}")
        return obtener_mensaje_error()
