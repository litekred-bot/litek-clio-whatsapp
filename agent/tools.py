# agent/tools.py — Calculadora de precios LiTek (matemáticas exactas, sin IA)
# Generado por AgentKit para LiTek

"""
Todas las fórmulas de pricing de LiTek en código Python puro.
Claude llama a calcular_precio() como herramienta — nunca calcula mentalmente.
Actualizar precios aquí = cambia en toda la app automáticamente.
"""

import logging

logger = logging.getLogger("agentkit")

# ─────────────────────────────────────────────────────────────────────────────
# Lonas promocionales — precio fijo por medida EXACTA
# ─────────────────────────────────────────────────────────────────────────────
_LONAS_PROMO: dict[tuple[int, int], float] = {
    (75, 50):   39,
    (100, 75):  99,
    (150, 100): 198,
    (200, 100): 264,
    (200, 150): 396,
    (150, 150): 297,
}


def _tasa(area: float, tabla: list[tuple[float, float]]) -> float:
    """Devuelve la tasa $/m² que corresponde al área según la tabla de rangos."""
    for limite, tasa in tabla:
        if area <= limite:
            return tasa
    return tabla[-1][1]


def calcular_precio(
    producto: str,
    base_cm: float,
    alto_cm: float,
    cantidad: int = 1,
    expres: bool = False,
) -> dict:
    """
    Calcula el precio exacto de cualquier producto LiTek.

    Args:
        producto: lona | vinil_impreso | vinil_pvc | coroplast | microperforado |
                  papel_couche | papel_bond | etiqueta_5x5 | etiqueta_personalizada |
                  tabloide_laser | corte_vinil
        base_cm : Ancho en cm. Para corte_vinil: longitud total en cm.
        alto_cm : Alto en cm.  Para corte_vinil: no se usa.
        cantidad: Número de piezas (default 1).
        expres  : True → aplica +35% al total.

    Returns:
        {"precio": float, ...}  o  {"accion": "escalar_asesor"|"error", "mensaje": str}
    """
    base_m     = base_cm / 100
    alto_m     = alto_cm / 100
    area_pieza = base_m * alto_m
    precio     = 0.0

    # ── LONA ─────────────────────────────────────────────────────────────────
    if producto == "lona":
        # Verificar precio promocional (acepta medidas giradas: 75×50 = 50×75)
        clave     = (int(base_cm), int(alto_cm))
        clave_inv = (int(alto_cm), int(base_cm))
        precio_promo = _LONAS_PROMO.get(clave) or _LONAS_PROMO.get(clave_inv)

        if precio_promo is not None:
            # Precio promo × cantidad (cada pieza al precio fijo)
            precio = float(precio_promo) * cantidad
        else:
            tabla = [
                (2.00,        252.0),
                (4.00,        150.0),
                (6.00,        144.0),
                (9.00,        130.0),
                (12.00,       125.0),
                (15.00,       115.0),
                (float("inf"), 99.0),
            ]
            area_total = area_pieza * cantidad
            precio = max(99.0, area_total * _tasa(area_total, tabla))

    # ── VINIL IMPRESO ────────────────────────────────────────────────────────
    elif producto == "vinil_impreso":
        tabla = [
            (1.00,        388.0),
            (2.00,        300.0),
            (4.00,        280.0),
            (6.00,        250.0),
            (8.00,        220.0),
            (float("inf"), 200.0),
        ]
        area_total = area_pieza * cantidad
        precio = max(188.0, area_total * _tasa(area_total, tabla))

    # ── VINIL SOBRE PVC 3mm ──────────────────────────────────────────────────
    elif producto == "vinil_pvc":
        area_total = area_pieza * cantidad
        if area_total <= 0.72:
            precio = 299.0
        else:
            tabla = [
                (1.40,        750.0),
                (2.88,        710.0),
                (5.76,        650.0),
                (float("inf"), 610.0),
            ]
            precio = area_total * _tasa(area_total, tabla)

    # ── COROPLAST — precio POR PIEZA individual ──────────────────────────────
    elif producto == "coroplast":
        def _pieza(area: float) -> float:
            if area <= 0.40:
                return 264.60
            elif area < 1.00:
                return area * 681.0
            else:
                # Redondeo estándar (no banker's): round(1.5)→2, round(2.5)→3
                redondeado = int(area + 0.5)
                return float(redondeado) * 735.0

        precio = sum(_pieza(area_pieza) for _ in range(cantidad))

    # ── MICROPERFORADO ───────────────────────────────────────────────────────
    elif producto == "microperforado":
        tabla = [
            (1.00,        560.0),
            (2.00,        300.0),
            (4.00,        250.0),
            (float("inf"), 210.0),
        ]
        area_total = area_pieza * cantidad
        precio = max(350.0, area_total * _tasa(area_total, tabla))

    # ── PAPEL COUCHÉ — lógica de 3 escalones ────────────────────────────────
    elif producto == "papel_couche":
        tabla = [
            (2.00,        310.00),
            (4.00,        227.95),
            (6.00,        217.95),
            (9.00,        196.76),
            (12.00,       189.20),
            (15.00,       174.08),
            (float("inf"), 143.81),
        ]
        area_total = area_pieza * cantidad
        calculado  = area_total * _tasa(area_total, tabla)
        if calculado < 110.0:
            precio = 110.0
        elif calculado <= 310.0:
            precio = 310.0
        else:
            precio = calculado

    # ── PAPEL BOND ───────────────────────────────────────────────────────────
    elif producto == "papel_bond":
        tabla = [
            (2.00,        299.00),
            (4.00,        242.46),
            (6.00,        232.74),
            (9.00,        210.11),
            (12.00,       202.03),
            (15.00,       185.89),
            (float("inf"), 153.57),
        ]
        area_total = area_pieza * cantidad
        precio = max(180.0, area_total * _tasa(area_total, tabla))

    # ── ETIQUETAS VINIL 5×5 cm (PROD-050) ───────────────────────────────────
    elif producto == "etiqueta_5x5":
        if cantidad < 40:
            return {"accion": "error", "mensaje": "Mínimo de pedido: 40 piezas ($150)"}
        if cantidad < 200:
            precio = max(150.0, cantidad * 3.75)
        elif cantidad < 400:
            precio = cantidad * 2.38
        else:
            precio = cantidad * 1.45

    # ── ETIQUETAS PERSONALIZADAS con suaje (PROD-051) ────────────────────────
    elif producto == "etiqueta_personalizada":
        if cantidad > 500:
            return {
                "accion": "escalar_asesor",
                "mensaje": "Pedido mayor a 500 etiquetas personalizadas — escalar a asesor"
            }
        tabla = [
            (0.24,        355.00),
            (0.49,       1500.00),
            (0.74,        952.00),
            (0.99,        710.00),
            (1.24,        581.57),
            (1.49,        489.21),
            (1.74,        460.00),
            (1.99,        430.00),
            (2.24,        410.00),
            (2.49,        380.00),
            (float("inf"), 370.00),
        ]
        area_total = area_pieza * cantidad
        precio = max(85.20, area_total * _tasa(area_total, tabla))

    # ── TABLOIDE LÁSER (por pieza) ───────────────────────────────────────────
    elif producto == "tabloide_laser":
        if cantidad <= 10:
            precio = cantidad * 27.0
        elif cantidad <= 30:
            precio = cantidad * 20.0
        elif cantidad <= 50:
            precio = cantidad * 17.0
        else:
            precio = cantidad * 13.50

    # ── CORTE VINIL (metros lineales) ────────────────────────────────────────
    # base_cm = longitud total en cm  (ej. 150 cm = 1.5 m lineales)
    elif producto == "corte_vinil":
        ml    = base_cm / 100
        tabla = [
            (1.00,        420.0),
            (2.00,        350.0),
            (4.00,        300.0),
            (8.00,        200.0),
            (float("inf"), 180.0),
        ]
        precio = max(120.0, ml * _tasa(ml, tabla))

    else:
        return {
            "accion": "error",
            "mensaje": (
                f"Producto '{producto}' no reconocido. "
                "Opciones válidas: lona, vinil_impreso, vinil_pvc, coroplast, microperforado, "
                "papel_couche, papel_bond, etiqueta_5x5, etiqueta_personalizada, "
                "tabloide_laser, corte_vinil"
            )
        }

    # Servicio exprés +35%
    if expres:
        precio *= 1.35

    return {
        "precio":    round(precio, 2),
        "producto":  producto,
        "base_cm":   base_cm,
        "alto_cm":   alto_cm,
        "cantidad":  cantidad,
        "expres":    expres,
    }
