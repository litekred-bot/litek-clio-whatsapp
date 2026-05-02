# agent/tools.py — Herramientas de cotización y ventas para LiTek
# Generado por AgentKit para LiTek

import os
import yaml
import logging

logger = logging.getLogger("agentkit")


def cargar_info_negocio() -> dict:
    """Carga la información del negocio desde business.yaml."""
    try:
        with open("config/business.yaml", "r", encoding="utf-8") as f:
            return yaml.safe_load(f)
    except FileNotFoundError:
        logger.error("config/business.yaml no encontrado")
        return {}


def obtener_horario() -> dict:
    """Retorna el horario de atención del negocio."""
    info = cargar_info_negocio()
    return {
        "horario": info.get("negocio", {}).get("horario", "No disponible"),
        "bot_disponible": "24/7",
    }


def calcular_precio_lona(base_cm: float, alto_cm: float, cantidad: int = 1, expres: bool = False) -> dict:
    """
    Calcula el precio de una lona según las tablas de LiTek.

    Args:
        base_cm: Ancho en centímetros
        alto_cm: Alto en centímetros
        cantidad: Número de piezas
        expres: Si aplica servicio exprés (+35%)

    Returns:
        Diccionario con área, precio unitario, total y desglose
    """
    area_m2 = (base_cm / 100) * (alto_cm / 100)

    # Tabla de precios por m²
    tabla = [
        (0, 0.49, 99),
        (0.50, 0.98, 185),
        (0.99, 2.00, 166),
        (2.01, 4.00, 150),
        (4.01, 6.00, 144),
        (6.01, 9.00, 130),
        (9.01, 12.00, 125),
        (12.01, 15.00, 115),
        (15.01, 100, 95),
        (100.01, 200, 80),
        (200.01, 400, 75),
        (400.01, float("inf"), 65),
    ]

    precio_m2 = 99  # mínimo
    for desde, hasta, precio in tabla:
        if desde <= area_m2 <= hasta:
            precio_m2 = precio
            break

    subtotal = max(area_m2 * precio_m2, 99)  # cobro mínimo $99
    total = subtotal * cantidad

    if expres:
        total = total * 1.35

    return {
        "area_m2": round(area_m2, 2),
        "precio_m2": precio_m2,
        "subtotal_unitario": round(subtotal, 2),
        "cantidad": cantidad,
        "expres": expres,
        "total": round(total, 2),
    }


def calcular_precio_vinil_impreso(base_cm: float, alto_cm: float, cantidad: int = 1, expres: bool = False) -> dict:
    """Calcula el precio de vinil impreso según las tablas de LiTek."""
    area_m2 = (base_cm / 100) * (alto_cm / 100)

    tabla = [
        (0, 0.56, 188),
        (0.57, 1.00, 388),
        (1.01, 2.00, 300),
        (2.01, 4.00, 280),
        (4.01, 6.00, 250),
        (6.01, 8.00, 220),
        (8.01, float("inf"), 200),
    ]

    precio_m2 = 188
    for desde, hasta, precio in tabla:
        if desde <= area_m2 <= hasta:
            precio_m2 = precio
            break

    subtotal = max(area_m2 * precio_m2, 188)
    total = subtotal * cantidad

    if expres:
        total = total * 1.35

    return {
        "area_m2": round(area_m2, 2),
        "precio_m2": precio_m2,
        "subtotal_unitario": round(subtotal, 2),
        "cantidad": cantidad,
        "expres": expres,
        "total": round(total, 2),
    }


def obtener_lona_promocional(base_cm: int, alto_cm: int) -> dict | None:
    """
    Verifica si las medidas corresponden a una lona promocional de precio fijo.
    Retorna el precio fijo si aplica, None si no hay promoción para esas medidas.
    """
    promos = {
        (75, 50): 39,
        (100, 75): 99,
        (150, 100): 198,
        (200, 100): 264,
        (200, 150): 396,
        (150, 150): 297,
    }
    return promos.get((int(base_cm), int(alto_cm)))


def buscar_en_knowledge(consulta: str) -> str:
    """
    Busca información relevante en los archivos de /knowledge.
    Retorna el contenido más relevante encontrado.
    """
    resultados = []
    knowledge_dir = "knowledge"

    if not os.path.exists(knowledge_dir):
        return "No hay archivos de conocimiento disponibles."

    for archivo in os.listdir(knowledge_dir):
        ruta = os.path.join(knowledge_dir, archivo)
        if archivo.startswith(".") or not os.path.isfile(ruta):
            continue
        try:
            with open(ruta, "r", encoding="utf-8") as f:
                contenido = f.read()
                if consulta.lower() in contenido.lower():
                    resultados.append(f"[{archivo}]: {contenido[:500]}")
        except (UnicodeDecodeError, IOError):
            continue

    if resultados:
        return "\n---\n".join(resultados)
    return "No encontré información específica sobre eso en mis archivos."
