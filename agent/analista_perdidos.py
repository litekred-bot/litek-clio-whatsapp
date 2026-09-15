# agent/analista_perdidos.py — Analista de clientes PERDIDOS (con IA)
# Generado por AgentKit para LiTek

"""
Lee las conversaciones de los clientes que se cayeron (No contestó, No concretó,
Esperando pago) y con Claude entrega:
  1) Diagnóstico por cliente (motivo + una línea).
  2) Patrones agrupados (motivo → cuántos, %).
  3) Recomendaciones concretas para vender más.

Control de costo: clasifica en LOTES con Haiku (barato) y hace UNA sola llamada
a Sonnet para las recomendaciones. A demanda (no automático).
"""

import os
import json
import logging
from anthropic import AsyncAnthropic
from agent.memory import clientes_perdidos, obtener_conversacion_crm

logger = logging.getLogger("agentkit")
_client = AsyncAnthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))

MODELO_CLASIFICA = "claude-haiku-4-5-20251001"  # barato, para clasificar cada chat
MODELO_RESUMEN   = "claude-sonnet-4-6"          # una sola llamada para recomendaciones

LOTE = 10  # clientes por llamada de clasificación

# Motivos válidos → etiqueta bonita para el panel
MOTIVOS = {
    "precio":               "💰 Precio (lo vieron caro)",
    "tiempo_entrega":       "⏰ Tiempo de entrega",
    "solo_pregunto":        "🤔 Solo preguntó, no decidió",
    "pidio_cuenta_no_pago": "💳 Pidió cuenta y no pagó",
    "factura_tramite":      "🧾 Factura / trámite",
    "no_respondio_saludo":  "👻 No respondió ni el saludo",
    "cambio_de_opinion":    "🔄 Cambió de opinión",
    "otro":                 "❓ Otro / no claro",
}


def _transcript(msgs: list[dict], max_msgs: int = 16, max_chars: int = 1600) -> str:
    """Arma un transcript compacto (últimos mensajes) para mandar a la IA."""
    ult = msgs[-max_msgs:]
    lineas = []
    for m in ult:
        who = "Cliente" if m.get("role") == "user" else "Clio"
        txt = (m.get("content") or "").replace("\n", " ").strip()
        if txt:
            lineas.append(f"{who}: {txt}")
    t = "\n".join(lineas)
    return t[-max_chars:]


async def _clasificar_lote(lote: list[dict]) -> list[dict]:
    """Clasifica un lote de chats. Devuelve [{i, motivo, diagnostico}]."""
    partes = []
    for i, c in enumerate(lote):
        partes.append(
            f"--- CLIENTE {i} (estado actual: {c['estado']}) ---\n{c['transcript']}"
        )
    bloque = "\n\n".join(partes)
    motivos_lista = ", ".join(MOTIVOS.keys())
    prompt = (
        "Eres analista de ventas de LiTek (imprenta/publicidad). Abajo hay conversaciones de "
        "clientes que NO cerraron la compra. Para CADA cliente identifica POR QUÉ no concretó.\n\n"
        f"Elige el motivo SOLO de esta lista: {motivos_lista}.\n"
        "Y escribe un 'diagnostico' de UNA línea, concreto (qué pasó).\n\n"
        "Responde SOLO con un JSON válido, sin texto extra, con esta forma:\n"
        '{\"resultados\": [{\"i\": 0, \"motivo\": \"precio\", \"diagnostico\": \"...\"}, ...]}\n\n'
        f"{bloque}"
    )
    try:
        resp = await _client.messages.create(
            model=MODELO_CLASIFICA,
            max_tokens=1500,
            messages=[{"role": "user", "content": prompt}],
        )
        texto = resp.content[0].text.strip()
        # Extraer el JSON (por si viene con ```json)
        ini = texto.find("{")
        fin = texto.rfind("}")
        data = json.loads(texto[ini:fin + 1])
        out = []
        for r in data.get("resultados", []):
            idx = int(r.get("i", -1))
            mot = r.get("motivo", "otro")
            if mot not in MOTIVOS:
                mot = "otro"
            if 0 <= idx < len(lote):
                out.append({"i": idx, "motivo": mot, "diagnostico": (r.get("diagnostico") or "")[:200]})
        return out
    except Exception as e:
        logger.error(f"Analista perdidos — error clasificando lote: {e}")
        # Si falla, marca todos como 'otro' para no perderlos
        return [{"i": i, "motivo": "otro", "diagnostico": "No se pudo analizar."} for i in range(len(lote))]


async def _recomendaciones(por_motivo: list[dict], total: int) -> str:
    """Una sola llamada a Sonnet: recomendaciones accionables para LiTek."""
    if not por_motivo:
        return ""
    resumen = "\n".join(f"- {m['label']}: {m['count']} clientes ({m['pct']}%)" for m in por_motivo)
    prompt = (
        "Eres consultor de ventas de LiTek (imprenta y publicidad visual en Campeche). "
        f"De {total} clientes que NO cerraron compra, estos son los motivos:\n{resumen}\n\n"
        "Dame 3 o 4 recomendaciones CONCRETAS y accionables (una frase cada una) para que su "
        "agente de WhatsApp 'Clio' recupere más ventas, atacando los motivos más frecuentes. "
        "Habla directo y práctico, en español, sin relleno. Devuelve solo las recomendaciones "
        "como lista con viñetas '- '."
    )
    try:
        resp = await _client.messages.create(
            model=MODELO_RESUMEN,
            max_tokens=500,
            messages=[{"role": "user", "content": prompt}],
        )
        return resp.content[0].text.strip()
    except Exception as e:
        logger.error(f"Analista perdidos — error recomendaciones: {e}")
        return ""


async def analizar_perdidos(desde=None, hasta=None, sucursal: str = "", asesor: str = "", limite: int = 60) -> dict:
    """Orquesta el análisis completo. Devuelve el reporte para el panel."""
    clientes = await clientes_perdidos(desde=desde, hasta=hasta, sucursal=sucursal, asesor=asesor, limite=limite)
    if not clientes:
        return {
            "total": 0, "por_motivo": [], "clientes": [],
            "recomendaciones": "No hay clientes perdidos en este periodo. 🎉",
        }

    # Traer transcript de cada cliente
    for c in clientes:
        conv = await obtener_conversacion_crm(c["telefono"], limite=40)
        c["transcript"] = _transcript(conv) or "(sin conversación registrada)"

    # Clasificar en lotes
    clasif: dict[int, dict] = {}
    for ini in range(0, len(clientes), LOTE):
        lote = clientes[ini:ini + LOTE]
        for r in await _clasificar_lote(lote):
            clasif[ini + r["i"]] = {"motivo": r["motivo"], "diagnostico": r["diagnostico"]}

    # Armar salida por cliente + conteo por motivo
    conteo: dict[str, int] = {}
    salida_clientes = []
    for idx, c in enumerate(clientes):
        info = clasif.get(idx, {"motivo": "otro", "diagnostico": ""})
        mot = info["motivo"]
        conteo[mot] = conteo.get(mot, 0) + 1
        salida_clientes.append({
            "nombre": c["nombre"],
            "telefono": c["telefono"],
            "estado": c["estado"],
            "asesor": c.get("asesor", ""),
            "sucursal": c["sucursal"],
            "motivo": mot,
            "motivo_label": MOTIVOS.get(mot, mot),
            "diagnostico": info["diagnostico"],
        })

    total = len(clientes)
    por_motivo = [
        {"motivo": m, "label": MOTIVOS.get(m, m), "count": n, "pct": round(n * 100 / total)}
        for m, n in sorted(conteo.items(), key=lambda kv: kv[1], reverse=True)
    ]

    recomendaciones = await _recomendaciones(por_motivo, total)

    return {
        "total": total,
        "por_motivo": por_motivo,
        "clientes": salida_clientes,
        "recomendaciones": recomendaciones,
    }
