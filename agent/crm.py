# agent/crm.py — Panel CRM web para el equipo LiTek
# Generado por AgentKit para LiTek
#
# Panel web donde el equipo ve y da seguimiento a leads, escalaciones,
# pedidos y ganadores de ruleta. Login por persona, estados editables.

import os
import time
import hmac
import hashlib
import base64
import json

_CRM_SECRET = os.getenv("CRM_SECRET", os.getenv("CRM_SALT", "litek-clio-crm-2026"))
_TOKEN_DIAS = 7  # el token de sesión dura 7 días


# ─────────────────────────────────────────────────────────────────────────────
# Tokens de sesión (firmados con HMAC)
# ─────────────────────────────────────────────────────────────────────────────
def crear_token(usuario: str, nombre: str) -> str:
    """Crea un token de sesión firmado."""
    payload = json.dumps({"u": usuario, "n": nombre, "exp": int(time.time()) + _TOKEN_DIAS * 86400})
    payload_b64 = base64.urlsafe_b64encode(payload.encode()).decode().rstrip("=")
    firma = hmac.new(_CRM_SECRET.encode(), payload_b64.encode(), hashlib.sha256).hexdigest()[:32]
    return f"{payload_b64}.{firma}"


def verificar_token(token: str) -> dict | None:
    """Verifica un token de sesión. Retorna {usuario, nombre} o None."""
    if not token or "." not in token:
        return None
    try:
        payload_b64, firma = token.rsplit(".", 1)
        firma_esperada = hmac.new(_CRM_SECRET.encode(), payload_b64.encode(), hashlib.sha256).hexdigest()[:32]
        if not hmac.compare_digest(firma, firma_esperada):
            return None
        padded = payload_b64 + "=" * (-len(payload_b64) % 4)
        data = json.loads(base64.urlsafe_b64decode(padded).decode())
        if data.get("exp", 0) < time.time():
            return None
        return {"usuario": data["u"], "nombre": data["n"]}
    except Exception:
        return None


# ─────────────────────────────────────────────────────────────────────────────
# HTML del panel (login + tabla, SPA simple)
# ─────────────────────────────────────────────────────────────────────────────
HTML_PANEL = r"""<!DOCTYPE html>
<html lang="es">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>CRM LiTek — Clio</title>
<style>
  * { box-sizing: border-box; margin: 0; padding: 0; font-family: -apple-system, Arial, sans-serif; }
  body { background: #f4f5f7; color: #222; }
  .top { background: #e30613; color: #fff; padding: 14px 20px; display: flex; justify-content: space-between; align-items: center; }
  .top h1 { font-size: 20px; }
  .top .user { font-size: 14px; }
  .top button { background: rgba(255,255,255,.2); color: #fff; border: none; padding: 6px 12px; border-radius: 6px; cursor: pointer; }
  .wrap { max-width: 1200px; margin: 0 auto; padding: 16px; }
  /* Login */
  #login { max-width: 360px; margin: 80px auto; background: #fff; padding: 30px; border-radius: 12px; box-shadow: 0 4px 20px rgba(0,0,0,.08); text-align: center; }
  #login h2 { color: #e30613; margin-bottom: 6px; }
  #login p { color: #777; font-size: 14px; margin-bottom: 18px; }
  #login input { width: 100%; padding: 12px; margin: 8px 0; border: 1px solid #ccc; border-radius: 8px; font-size: 15px; }
  #login button { width: 100%; background: #e30613; color: #fff; border: none; padding: 13px; border-radius: 8px; font-size: 16px; font-weight: bold; cursor: pointer; margin-top: 8px; }
  #login .err { color: #e30613; font-size: 14px; margin-top: 10px; min-height: 18px; }
  /* Filtros */
  .filtros { display: flex; gap: 8px; flex-wrap: wrap; margin: 14px 0; }
  .filtros button { background: #fff; border: 1px solid #ddd; padding: 8px 14px; border-radius: 20px; cursor: pointer; font-size: 14px; }
  .filtros button.activo { background: #e30613; color: #fff; border-color: #e30613; }
  .sucursales { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; background: #fff; border: 1px solid #e7e1d7; border-radius: 12px; padding: 10px 14px; margin-bottom: 12px; box-shadow: 0 1px 4px rgba(0,0,0,.05); }
  .sucursales .suc-label { font-weight: bold; color: #161616; font-size: 14px; margin-right: 4px; }
  .sucursales button { background: #f4f1ea; border: 1px solid #ddd; padding: 7px 16px; border-radius: 20px; cursor: pointer; font-size: 14px; color: #444; }
  .sucursales button.activo { background: #161616; color: #fff; border-color: #161616; }
  .card .suc-badge { display: inline-block; font-size: 11px; font-weight: bold; border-radius: 10px; padding: 2px 9px; margin-left: 6px; background: #ECECEC; color: #444; }
  .card .fact-badge { display: inline-block; font-size: 11px; font-weight: bold; border-radius: 10px; padding: 2px 9px; margin-left: 6px; }
  .card .fact-pend { background: #fff3e0; color: #e65100; }
  .card .fact-ok { background: #e8f5e9; color: #2e7d32; }
  .card .acciones .bfact { background: #fff3e0; color: #e65100; border: 1px solid #ffb74d; border-radius: 6px; padding: 6px 10px; font-size: 13px; cursor: pointer; }
  .card .acciones .bfact.on { background: #2e7d32; color: #fff; border-color: #2e7d32; }
  .carga { display: flex; gap: 10px; flex-wrap: wrap; margin: 14px 0 6px; }
  .carga .a { background: #fff3e0; border: 1px solid #ffcc80; border-radius: 20px; padding: 8px 16px; font-size: 14px; cursor: pointer; }
  .carga .a b { color: #e65100; font-size: 17px; }
  .carga .titulo { width: 100%; font-size: 12px; color: #888; margin-bottom: 2px; }
  .buscador { width: 100%; padding: 11px 14px; border: 1px solid #ccc; border-radius: 10px; font-size: 15px; margin: 6px 0 12px; }
  .stats { display: flex; gap: 10px; flex-wrap: wrap; margin-bottom: 14px; }
  .stat { background: #fff; border-radius: 10px; padding: 12px 18px; box-shadow: 0 1px 4px rgba(0,0,0,.05); }
  .stat b { font-size: 22px; display: block; color: #e30613; }
  .stat span { font-size: 12px; color: #777; }
  .stat.venta { background: #e8f5e9; }
  .stat.venta b { color: #2e7d32; }
  .stat.venta.hoy { background: #fff8e1; }
  .stat.venta.hoy b { color: #e65100; }
  .stat.venta.total { background: #e3f2fd; }
  .stat.venta.total b { color: #1565c0; }
  .ventas-filtro { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; margin-bottom: 14px; font-size: 13px; color: #555; }
  .ventas-filtro span { font-weight: 600; }
  .ventas-filtro input[type=month] { padding: 5px 8px; border: 1px solid #ddd; border-radius: 6px; font-size: 13px; }
  .ventas-filtro button { padding: 5px 12px; border: 1px solid #a5d6a7; background: #e8f5e9; color: #2e7d32; border-radius: 6px; font-size: 13px; cursor: pointer; }
  /* Tarjetas */
  .card { background: #fff; border-radius: 10px; padding: 14px; margin-bottom: 10px; box-shadow: 0 1px 4px rgba(0,0,0,.05); border-left: 4px solid #ccc; }
  .card.nuevo { border-left-color: #2196F3; }
  .card.asignado { border-left-color: #FF9800; }
  .card.proceso { border-left-color: #9C27B0; }
  .card.vendido { border-left-color: #4CAF50; opacity: .75; }
  .card.no_contesto { border-left-color: #555; opacity: .65; }
  .card.no_concretado { border-left-color: #d4a017; opacity: .8; }
  .card.esperando_pago { border-left-color: #00897b; background: #e0f2f1; }
  .costos-box { display:flex; gap:14px; flex-wrap:wrap; align-items:center; background:#fff; border:1px solid #e7e1d7; border-radius:12px; padding:14px; margin-bottom:14px; }
  .costos-box label { font-size:14px; color:#333; }
  .costos-box input { width:110px; padding:7px 9px; border:1px solid #ccc; border-radius:8px; font-size:14px; margin-left:4px; }
  .costos-box .costo-tot { margin-left:auto; font-size:15px; color:#161616; }
  .tabla-analisis { width:100%; border-collapse:collapse; background:#fff; border-radius:10px; overflow:hidden; font-size:14px; }
  .tabla-analisis th { background:#161616; color:#fff; padding:9px 8px; text-align:right; font-weight:600; }
  .tabla-analisis th:first-child { text-align:left; }
  .tabla-analisis td { padding:9px 8px; text-align:right; border-bottom:1px solid #eee; }
  .tabla-analisis td:first-child { text-align:left; }
  .card .head { display: flex; justify-content: space-between; align-items: start; flex-wrap: wrap; gap: 8px; }
  .card .nombre { font-weight: bold; font-size: 16px; }
  .card .tipo { font-size: 11px; padding: 2px 8px; border-radius: 10px; background: #eee; color: #555; text-transform: uppercase; }
  .card.alertado { border-left-color: #e30613; background: #fff6f6; }
  .card.expres-on { box-shadow: 0 0 0 2px #ff9800, 0 1px 4px rgba(0,0,0,.05); }
  .card .expres-badge { display: inline-block; background: #ff9800; color: #fff; font-size: 11px; font-weight: bold; border-radius: 10px; padding: 2px 9px; margin-left: 6px; }
  .card .diseno-badge { display: inline-block; background: #7e57c2; color: #fff; font-size: 11px; font-weight: bold; border-radius: 10px; padding: 2px 9px; margin-left: 6px; }
  .card .aprobado-badge { display: inline-block; background: #2e7d32; color: #fff; font-size: 11px; font-weight: bold; border-radius: 10px; padding: 2px 9px; margin-left: 6px; }
  .card .calif-badge { display: inline-block; font-size: 11px; font-weight: bold; border-radius: 10px; padding: 2px 9px; margin-left: 6px; }
  .card .calif-bueno { background: #e8f5e9; color: #2e7d32; }
  .card .calif-regular { background: #fff8e1; color: #f57f17; }
  .card .calif-malo { background: #ffebee; color: #c62828; }
  .resumen-calif { display: flex; gap: 10px; flex-wrap: wrap; margin-bottom: 12px; }
  .resumen-calif .rc { padding: 8px 14px; border-radius: 10px; font-size: 14px; font-weight: 600; }
  .resumen-calif .rc-b { background: #e8f5e9; color: #2e7d32; }
  .resumen-calif .rc-r { background: #fff8e1; color: #f57f17; }
  .resumen-calif .rc-m { background: #ffebee; color: #c62828; }
  .card .acciones .bexpres { background: #fff3e0; color: #e65100; border: 1px solid #ffb74d; border-radius: 6px; padding: 6px 10px; font-size: 13px; cursor: pointer; }
  .card .acciones .bexpres.on { background: #ff9800; color: #fff; border-color: #ff9800; }
  .card .acciones .bdiseno { background: #ede7f6; color: #5e35b1; border: 1px solid #b39ddb; border-radius: 6px; padding: 6px 10px; font-size: 13px; cursor: pointer; }
  .card .acciones .bdiseno.on { background: #7e57c2; color: #fff; border-color: #7e57c2; }
  .card .acciones .bentregar { background: #00897b; color: #fff; border: 1px solid #00897b; border-radius: 6px; padding: 6px 10px; font-size: 13px; cursor: pointer; }
  .entrega-badge { display:inline-block; background:#00897b; color:#fff; border-radius:10px; padding:2px 9px; font-size:11px; font-weight:bold; margin-left:6px; }
  .entrega-prog { display:inline-block; background:#e0f2f1; color:#00695c; border-radius:10px; padding:2px 9px; font-size:11px; margin-left:6px; }
  .card .acciones .baprob { background: #e8f5e9; color: #2e7d32; border: 1px solid #a5d6a7; border-radius: 6px; padding: 6px 10px; font-size: 13px; cursor: pointer; }
  .card .acciones .baprob.on { background: #2e7d32; color: #fff; border-color: #2e7d32; }
  .card .alerta-banner { background: #ffe0e0; color: #b71c1c; border-radius: 8px; padding: 7px 10px; margin: 8px 0; font-size: 13px; font-weight: bold; display: flex; justify-content: space-between; align-items: center; gap: 8px; }
  .card .alerta-banner button { background: #b71c1c; color: #fff; border: none; border-radius: 6px; padding: 4px 10px; font-size: 12px; cursor: pointer; }
  .card .desc { color: #444; font-size: 14px; margin: 8px 0; white-space: pre-wrap; }
  .card .meta { font-size: 12px; color: #999; }
  .card .acciones { display: flex; gap: 6px; flex-wrap: wrap; margin-top: 10px; align-items: center; }
  .card .acciones select, .card .acciones input { padding: 6px 8px; border: 1px solid #ddd; border-radius: 6px; font-size: 13px; }
  .card .acciones input.nota { flex: 1; min-width: 140px; }
  .card .acciones input.monto { width: 100px; font-weight: 600; color: #2e7d32; }
  .card a.wa, .card button.wa { background: #25D366; color: #fff; text-decoration: none; padding: 6px 12px; border-radius: 6px; font-size: 13px; border: none; cursor: pointer; }
  .card button.copia { background: #eef; color: #334; border: 1px solid #ccd; padding: 6px 12px; border-radius: 6px; font-size: 13px; cursor: pointer; }
  .vacio { text-align: center; color: #999; padding: 40px; }
  /* Modal de chat */
  .modal { display: none; position: fixed; inset: 0; background: rgba(0,0,0,.5); z-index: 100; align-items: center; justify-content: center; }
  .modal .box { background: #fff; border-radius: 12px; width: 92%; max-width: 520px; max-height: 82vh; display: flex; flex-direction: column; overflow: hidden; }
  .modal .mhead { padding: 14px; border-bottom: 1px solid #eee; display: flex; justify-content: space-between; align-items: center; }
  .modal .mhead b { font-size: 16px; }
  .modal .mhead button { background: #eee; border: none; border-radius: 6px; padding: 6px 12px; cursor: pointer; font-size: 14px; }
  .modal .chat { padding: 14px; overflow-y: auto; flex: 1; background: #ece5dd; }
  .burb { max-width: 82%; padding: 8px 11px; border-radius: 8px; margin-bottom: 8px; font-size: 14px; white-space: pre-wrap; box-shadow: 0 1px 1px rgba(0,0,0,.1); }
  .burb .quien { font-size: 10px; color: #777; margin-bottom: 3px; font-weight: bold; }
  .bcli { background: #fff; margin-right: auto; }
  .bclio { background: #dcf8c6; margin-left: auto; }
  .basesor { background: #cfe8ff; margin-left: auto; }
  .modal .mctrl { padding: 10px 14px; background: #fafafa; border-bottom: 1px solid #eee; display: flex; justify-content: space-between; align-items: center; gap: 8px; flex-wrap: wrap; }
  .modal .mctrl .est { font-size: 13px; }
  .modal .mctrl .est b { color: #e30613; }
  .modal .mctrl button { border: none; border-radius: 6px; padding: 7px 12px; cursor: pointer; font-size: 13px; font-weight: bold; }
  .modal .mctrl .tomar { background: #FF9800; color: #fff; }
  .modal .mctrl .devolver { background: #2196F3; color: #fff; }
  .modal .msend { display: flex; gap: 6px; padding: 10px; border-top: 1px solid #eee; }
  .modal .msend input { flex: 1; padding: 10px; border: 1px solid #ccc; border-radius: 8px; font-size: 14px; }
  .modal .msend button { background: #25D366; color: #fff; border: none; border-radius: 8px; padding: 10px 16px; cursor: pointer; font-weight: bold; }
</style>
</head>
<body>

<div id="login">
  <h2>🤖 CRM LiTek</h2>
  <p>Panel de seguimiento del equipo</p>
  <input id="u" placeholder="Usuario" autocomplete="username">
  <input id="p" type="password" placeholder="Contraseña" autocomplete="current-password">
  <button onclick="entrar()">Entrar</button>
  <div class="err" id="err"></div>
</div>

<div id="app" style="display:none">
  <div class="top">
    <h1>🤖 CRM LiTek</h1>
    <div><span class="user" id="quien"></span> &nbsp; <button id="btnAnalisis" onclick="toggleAnalisis()" style="display:none">📊 Análisis</button> &nbsp; <button id="btnEquipo" onclick="abrirEquipo()" style="display:none">👥 Equipo</button> &nbsp; <button onclick="salir()">Salir</button></div>
  </div>
  <div class="wrap">
    <div class="sucursales" id="sucursales">
      <span class="suc-label">🏢 Sucursal:</span>
      <button data-suc="" class="activo">Todas</button>
      <button data-suc="Campeche">Campeche</button>
      <button data-suc="Mérida">Mérida</button>
      <button data-suc="Carmen">Carmen</button>
    </div>
    <div class="carga" id="carga"></div>
    <input id="buscar" class="buscador" placeholder="🔍 Buscar por nombre o número..." oninput="aplicarBusqueda()">
    <div class="stats" id="stats"></div>
    <div class="ventas-filtro" id="ventasFiltro" style="display:none">
      <span>💰 Ventas:</span>
      <label>De <input type="month" id="vDesde" onchange="aplicarVentas()"></label>
      <label>a <input type="month" id="vHasta" onchange="aplicarVentas()"></label>
      <button id="btnVentasMes" onclick="ventasMesActual()">Este mes</button>
      <button id="btnVentasTodo" onclick="ventasTodo()">Todo</button>
    </div>
    <div class="filtros" id="filtros">
      <button data-f="estado" data-v="" class="activo">Todos</button>
      <button data-f="estado" data-v="nuevo">🔵 Nuevos</button>
      <button data-f="estado" data-v="asignado">🟠 Asignados</button>
      <button data-f="estado" data-v="proceso">🟣 En proceso</button>
      <button data-f="estado" data-v="vendido">✅ Vendidos</button>
      <button data-f="estado" data-v="esperando_pago">💳 Esperando pago</button>
      <button data-f="estado" data-v="no_contesto">⚫ No contestó</button>
      <button data-f="estado" data-v="no_concretado">🟡 No concretados</button>
    </div>
    <div class="filtros" id="filtros2">
      <button data-f="tipo" data-v="" class="activo">Todos los tipos</button>
      <button data-f="tipo" data-v="escalacion">Escalaciones</button>
      <button data-f="tipo" data-v="pedido">Pedidos</button>
      <button data-f="tipo" data-v="cliente">Clientes nuevos</button>
      <button data-f="tipo" data-v="ruleta">Ruleta</button>
    </div>
    <div class="filtros" id="filtros3">
      <button id="btnRevisar" onclick="toggleRevisar()" style="background:#ffe0e0;color:#b71c1c;border-color:#ffb3b3;">⚠️ Solo a revisar</button>
      <button id="btnExpres" onclick="toggleSoloExpres()" style="background:#fff3e0;color:#e65100;border-color:#ffb74d;">⚡ Solo exprés</button>
      <button id="btnCalif" onclick="toggleSoloCalif()" style="background:#e8f5e9;color:#2e7d32;border-color:#a5d6a7;">⭐ Calificaciones</button>
      <button id="btnFactura" onclick="toggleSoloFactura()" style="background:#fff3e0;color:#e65100;border-color:#ffb74d;">🧾 Por facturar</button>
      <button id="btnDiseno" onclick="toggleSoloDiseno()" style="background:#ede7f6;color:#5e35b1;border-color:#b39ddb;">🎨 Solo diseño</button>
      <button id="btnListo" onclick="toggleSoloListo()" style="background:#e0f2f1;color:#00695c;border-color:#80cbc4;">📦 Listo para entregar</button>
    </div>
    <div id="lista"></div>
    <div id="analisisPanel" style="display:none"></div>
  </div>
</div>

<div class="modal" id="modal" onclick="if(event.target===this)cerrarModal()">
  <div class="box">
    <div class="mhead"><b id="modalNombre">Conversación</b><button onclick="cerrarModal()">✕ Cerrar</button></div>
    <div class="mctrl" id="modalCtrl"></div>
    <div class="chat" id="modalChat"></div>
    <div class="msend">
      <input id="msgInput" placeholder="Escribe para responder al cliente..." onkeydown="if(event.key==='Enter')enviarMensaje()">
      <button onclick="enviarMensaje()">Enviar</button>
    </div>
  </div>
</div>

<div class="modal" id="modalEquipo" onclick="if(event.target===this)cerrarEquipo()">
  <div class="box">
    <div class="mhead"><b>👥 Equipo — Contraseñas</b><button onclick="cerrarEquipo()">✕ Cerrar</button></div>
    <div class="chat" id="equipoLista" style="background:#fff;"></div>
  </div>
</div>

<script>
var TOKEN = localStorage.getItem("crm_token") || "";
var fEstado = "", fTipo = "";
var ASESORES = ["", "Anna", "Brayan", "Tere", "Chino", "Alan", "Jadiel", "Edith", "Erick"];
var ESTADOS = {nuevo:"🔵 Nuevo", asignado:"🟠 Asignado", proceso:"🟣 En proceso", vendido:"✅ Vendido", esperando_pago:"💳 Esperando pago", no_contesto:"⚫ No contestó", no_concretado:"🟡 No concretado"};
var TIPOS = {escalacion:"Escalación", pedido:"Pedido", cliente:"Cliente", ruleta:"Ruleta"};

if (TOKEN) mostrarApp();

async function entrar(){
  var u = document.getElementById("u").value.trim();
  var p = document.getElementById("p").value;
  var err = document.getElementById("err");
  err.textContent = "";
  try{
    var r = await fetch("/crm/login", {method:"POST", headers:{"Content-Type":"application/json"}, body: JSON.stringify({usuario:u, password:p})});
    var d = await r.json();
    if (d.ok){ TOKEN = d.token; localStorage.setItem("crm_token", TOKEN); mostrarApp(); }
    else err.textContent = "Usuario o contraseña incorrectos";
  }catch(e){ err.textContent = "Error de conexión"; }
}

function salir(){ localStorage.removeItem("crm_token"); location.reload(); }

function mostrarApp(){
  document.getElementById("login").style.display = "none";
  document.getElementById("app").style.display = "block";
  document.querySelectorAll("#filtros button, #filtros2 button").forEach(function(b){
    b.onclick = function(){
      var grupo = b.parentElement.querySelectorAll("button");
      grupo.forEach(function(x){ x.classList.remove("activo"); });
      b.classList.add("activo");
      if (b.dataset.f === "estado") fEstado = b.dataset.v; else fTipo = b.dataset.v;
      cargar();
    };
  });
  document.querySelectorAll("#sucursales button").forEach(function(b){
    b.onclick = function(){
      document.querySelectorAll("#sucursales button").forEach(function(x){ x.classList.remove("activo"); });
      b.classList.add("activo");
      SUCURSAL = b.dataset.suc;
      cargar();
    };
  });
  initVentas();   // arranca en el mes actual
  cargar();
}

var V_DESDE = "", V_HASTA = "", V_TODO = false;
var SUCURSAL = "";

function mesActualStr(){
  var d = new Date();
  return d.getFullYear() + "-" + String(d.getMonth()+1).padStart(2,"0");
}
function initVentas(){
  var m = mesActualStr();
  V_DESDE = m; V_HASTA = m; V_TODO = false;
  var a = document.getElementById("vDesde"), b = document.getElementById("vHasta");
  if (a) a.value = m;
  if (b) b.value = m;
}
function redibujar(){ if (MODO_ANALISIS){ cargarAnalisis(); } else { cargar(); } }
function aplicarVentas(){
  V_TODO = false;
  V_DESDE = document.getElementById("vDesde").value || "";
  V_HASTA = document.getElementById("vHasta").value || "";
  resaltarTodo(false);
  redibujar();
}
function ventasMesActual(){
  initVentas();
  resaltarTodo(false);
  redibujar();
}
function ventasTodo(){
  V_TODO = true;
  resaltarTodo(true);
  redibujar();
}
function resaltarTodo(on){
  var b = document.getElementById("btnVentasTodo");
  if (!b) return;
  b.style.background = on ? "#2e7d32" : "";
  b.style.color = on ? "#fff" : "";
}

async function cargar(){
  try{
    var url = "/crm/api/registros?estado=" + encodeURIComponent(fEstado) + "&tipo=" + encodeURIComponent(fTipo) + "&sucursal=" + encodeURIComponent(SUCURSAL);
    if (V_TODO){ url += "&desde=todo&hasta=todo"; }
    else if (V_DESDE || V_HASTA){ url += "&desde=" + encodeURIComponent(V_DESDE||V_HASTA) + "&hasta=" + encodeURIComponent(V_HASTA||V_DESDE); }
    var r = await fetch(url, {headers:{"Authorization":"Bearer "+TOKEN}});
    if (r.status === 401){ salir(); return; }
    var d = await r.json();
    document.getElementById("quien").textContent = "👤 " + (d.nombre || "") + (d.es_director ? "" : " · viendo solo lo tuyo");
    document.getElementById("btnEquipo").style.display = (d.es_director && !d.solo_sucursal) ? "inline-block" : "none";
    document.getElementById("btnAnalisis").style.display = d.es_director ? "inline-block" : "none";
    ES_DIRECTOR = !!d.es_director;
    document.getElementById("ventasFiltro").style.display = ES_DIRECTOR ? "flex" : "none";
    // Admin de UNA sola sucursal (ej. Leo→Carmen): se le fija y se oculta el selector.
    if (d.solo_sucursal){
      SUCURSAL = d.solo_sucursal;
      var sucBar = document.getElementById("sucursales");
      if (sucBar) sucBar.style.display = "none";
    }
    window._cargaCache = d.carga;
    pintarCarga(d.carga);
    pintarStats(d.stats, d.ventas);
    ULTIMOS_REGISTROS = d.registros || [];
    aplicarBusqueda();
  }catch(e){ console.log(e); }
}

// ── Panel de análisis de mensajería (solo director) ──
var MODO_ANALISIS = false;
var ANALISIS_DATA = null;
var _ELEMS_NORMALES = ["buscar","stats","filtros","filtros2","filtros3","lista","carga"];

function toggleAnalisis(){
  MODO_ANALISIS = !MODO_ANALISIS;
  _ELEMS_NORMALES.forEach(function(id){ var e=document.getElementById(id); if(e) e.style.display = MODO_ANALISIS ? "none" : ""; });
  document.getElementById("ventasFiltro").style.display = "flex"; // el filtro de mes se queda para elegir periodo
  document.getElementById("analisisPanel").style.display = MODO_ANALISIS ? "block" : "none";
  document.getElementById("btnAnalisis").textContent = MODO_ANALISIS ? "← Volver" : "📊 Análisis";
  if (MODO_ANALISIS){ cargarAnalisis(); } else { cargar(); }
}

function _claveCostos(rango){ return "costos_" + (rango||"mes"); }
function _costosGuardados(rango){ try{ return JSON.parse(localStorage.getItem(_claveCostos(rango))||"{}"); }catch(e){ return {}; } }
function _money(n){ return "$" + (Math.round(n||0)).toLocaleString("es-MX"); }

async function cargarAnalisis(){
  var panel = document.getElementById("analisisPanel");
  panel.innerHTML = "<div style='padding:20px;color:#777'>Calculando…</div>";
  var url = "/crm/api/analisis";
  if (V_TODO){ url += "?desde=todo&hasta=todo"; }
  else if (V_DESDE || V_HASTA){ url += "?desde=" + encodeURIComponent(V_DESDE||V_HASTA) + "&hasta=" + encodeURIComponent(V_HASTA||V_DESDE); }
  try{
    var r = await fetch(url, {headers:{"Authorization":"Bearer "+TOKEN}});
    if (r.status === 401){ salir(); return; }
    if (r.status === 403){ panel.innerHTML = "<div style='padding:20px'>Solo el director ve el análisis.</div>"; return; }
    ANALISIS_DATA = await r.json();
    pintarAnalisis();
  }catch(e){ console.log(e); panel.innerHTML = "<div style='padding:20px'>Error cargando el análisis.</div>"; }
}

function guardarCostos(){
  if(!ANALISIS_DATA) return;
  var c = {
    ia: parseFloat(document.getElementById("cIA").value)||0,
    whapi: parseFloat(document.getElementById("cWhapi").value)||0,
    railway: parseFloat(document.getElementById("cRailway").value)||0
  };
  localStorage.setItem(_claveCostos(ANALISIS_DATA.rango), JSON.stringify(c));
  pintarAnalisis();
}

function pintarAnalisis(){
  var d = ANALISIS_DATA; if(!d) return;
  var c = _costosGuardados(d.rango);
  var costoTotal = (c.ia||0) + (c.whapi||0) + (c.railway||0);
  var totMsg = d.total.mensajes || 0;
  function fila(nombre, x, esTotal){
    var share = esTotal ? 1 : (totMsg>0 ? (x.mensajes/totMsg) : 0);
    var costo = costoTotal * share;
    var ganancia = x.vendido - costo;
    var cpc = x.entraron>0 ? costo/x.entraron : 0;
    var cpv = x.ventas>0 ? costo/x.ventas : 0;
    var pct = x.vendido>0 ? (costo/x.vendido*100) : 0;
    var gcol = ganancia>=0 ? "#2e7d32" : "#c62828";
    var estilo = esTotal ? " style='background:#fff8e1;border-top:2px solid #e30613'" : "";
    var b0=esTotal?"<b>":"", b1=esTotal?"</b>":"";
    return "<tr"+estilo+">"+
      "<td>"+b0+nombre+b1+"</td>"+
      "<td>"+b0+x.entraron+b1+"</td>"+
      "<td>"+b0+x.mensajes+b1+"</td>"+
      "<td>"+b0+_money(x.vendido)+b1+"</td>"+
      "<td>"+b0+x.ventas+b1+"</td>"+
      "<td>"+b0+_money(costo)+b1+"</td>"+
      "<td style='color:"+gcol+";font-weight:bold'>"+_money(ganancia)+"</td>"+
      "<td>"+b0+_money(cpc)+b1+"</td>"+
      "<td>"+b0+_money(cpv)+b1+"</td>"+
      "<td>"+b0+pct.toFixed(0)+"%"+b1+"</td>"+
    "</tr>";
  }
  var sucs = ["Campeche","Carmen","Mérida"];
  var filas = sucs.map(function(s){ return fila(s, d.por_sucursal[s], false); }).join("");
  filas += fila("TOTAL", d.total, true);

  document.getElementById("analisisPanel").innerHTML =
    "<h2 style='margin:6px 0'>📊 Análisis de mensajería — "+d.rango+"</h2>"+
    "<p style='color:#777;margin:2px 0 14px'>Elige el mes con el filtro de 💰 Ventas de arriba. Escribe los costos del mes EN PESOS (es el total del negocio); se reparten por sucursal según sus mensajes.</p>"+
    "<div class='costos-box'>"+
      "<label>🤖 IA (Anthropic) $ <input id='cIA' type='number' min='0' placeholder='0' value='"+(c.ia||"")+"' oninput='guardarCostos()'></label>"+
      "<label>📱 Whapi $ <input id='cWhapi' type='number' min='0' placeholder='0' value='"+(c.whapi||"")+"' oninput='guardarCostos()'></label>"+
      "<label>☁️ Railway $ <input id='cRailway' type='number' min='0' placeholder='0' value='"+(c.railway||"")+"' oninput='guardarCostos()'></label>"+
      "<span class='costo-tot'>Costo total del mes: <b>"+_money(costoTotal)+"</b></span>"+
    "</div>"+
    "<div style='overflow-x:auto'><table class='tabla-analisis'><thead><tr>"+
      "<th>Sucursal</th><th>Entraron</th><th>Mensajes</th><th>Vendido</th><th>Ventas</th><th>Costo</th><th>Ganancia</th><th>$/conv</th><th>$/venta</th><th>% costo</th>"+
    "</tr></thead><tbody>"+filas+"</tbody></table></div>"+
    "<p style='color:#999;font-size:12px;margin-top:10px'>💡 \"Ganancia\" = Vendido − Costo. \"$/conv\" = costo por conversación. \"$/venta\" = costo por cada venta. \"% costo\" = qué parte de lo vendido se fue en mensajería.</p>";
}

var ULTIMOS_REGISTROS = [];
var ES_DIRECTOR = false;
var SOLO_REVISAR = false;
var SOLO_EXPRES = false;
var SOLO_CALIF = false;
var SOLO_FACTURA = false;
var SOLO_DISENO = false;
var SOLO_LISTO = false;
function aplicarBusqueda(){
  var q = (document.getElementById("buscar").value || "").toLowerCase().trim();
  var qDig = q.replace(/\D/g, "");
  var regs = ULTIMOS_REGISTROS;
  if (SOLO_REVISAR){ regs = regs.filter(function(r){ return r.alerta; }); }
  if (SOLO_EXPRES){ regs = regs.filter(function(r){ return r.expres; }); }
  if (SOLO_CALIF){ regs = regs.filter(function(r){ return r.calificacion; }); }
  if (SOLO_FACTURA){ regs = regs.filter(function(r){ return r.factura && !r.facturado; }); }
  if (SOLO_DISENO){ regs = regs.filter(function(r){ return r.diseno; }); }
  if (SOLO_LISTO){ regs = regs.filter(function(r){ return r.listo_entregar; }); }
  if (fAsesorChip){ regs = regs.filter(function(r){ return r.asesor === fAsesorChip && FINALES_JS.indexOf(r.estado) === -1; }); }
  if (q){
    regs = regs.filter(function(r){
      var nom = (r.nombre || "").toLowerCase();
      var tel = (r.telefono || "").replace(/\D/g, "");
      return nom.indexOf(q) !== -1 || (qDig && tel.indexOf(qDig) !== -1);
    });
  }
  pintar(regs);
}

function toggleRevisar(){
  SOLO_REVISAR = !SOLO_REVISAR;
  var b = document.getElementById("btnRevisar");
  b.style.background = SOLO_REVISAR ? "#b71c1c" : "#ffe0e0";
  b.style.color = SOLO_REVISAR ? "#fff" : "#b71c1c";
  aplicarBusqueda();
}

async function resolverAlerta(id){
  await fetch("/crm/api/registro/"+id, {method:"POST", headers:{"Authorization":"Bearer "+TOKEN, "Content-Type":"application/json"}, body: JSON.stringify({alerta: ""})});
  cargar();
}

function toggleSoloExpres(){
  SOLO_EXPRES = !SOLO_EXPRES;
  var b = document.getElementById("btnExpres");
  b.style.background = SOLO_EXPRES ? "#ff9800" : "#fff3e0";
  b.style.color = SOLO_EXPRES ? "#fff" : "#e65100";
  aplicarBusqueda();
}

function toggleSoloDiseno(){
  SOLO_DISENO = !SOLO_DISENO;
  var b = document.getElementById("btnDiseno");
  b.style.background = SOLO_DISENO ? "#7e57c2" : "#ede7f6";
  b.style.color = SOLO_DISENO ? "#fff" : "#5e35b1";
  aplicarBusqueda();
}

function toggleSoloListo(){
  SOLO_LISTO = !SOLO_LISTO;
  var b = document.getElementById("btnListo");
  b.style.background = SOLO_LISTO ? "#00897b" : "#e0f2f1";
  b.style.color = SOLO_LISTO ? "#fff" : "#00695c";
  aplicarBusqueda();
}

function toggleSoloFactura(){
  SOLO_FACTURA = !SOLO_FACTURA;
  var b = document.getElementById("btnFactura");
  b.style.background = SOLO_FACTURA ? "#e65100" : "#fff3e0";
  b.style.color = SOLO_FACTURA ? "#fff" : "#e65100";
  aplicarBusqueda();
}

function toggleSoloCalif(){
  SOLO_CALIF = !SOLO_CALIF;
  var b = document.getElementById("btnCalif");
  b.style.background = SOLO_CALIF ? "#2e7d32" : "#e8f5e9";
  b.style.color = SOLO_CALIF ? "#fff" : "#2e7d32";
  aplicarBusqueda();
}

async function toggleExpres(id, valor){
  await fetch("/crm/api/registro/"+id, {method:"POST", headers:{"Authorization":"Bearer "+TOKEN, "Content-Type":"application/json"}, body: JSON.stringify({expres: valor})});
  cargar();
}

async function toggleDiseno(id, valor){
  await fetch("/crm/api/registro/"+id, {method:"POST", headers:{"Authorization":"Bearer "+TOKEN, "Content-Type":"application/json"}, body: JSON.stringify({diseno: valor})});
  cargar();
}

async function toggleDisenoAprobado(id, valor){
  await fetch("/crm/api/registro/"+id, {method:"POST", headers:{"Authorization":"Bearer "+TOKEN, "Content-Type":"application/json"}, body: JSON.stringify({diseno_aprobado: valor})});
  cargar();
}

async function toggleFacturado(id, valor){
  await fetch("/crm/api/registro/"+id, {method:"POST", headers:{"Authorization":"Bearer "+TOKEN, "Content-Type":"application/json"}, body: JSON.stringify({facturado: valor})});
  cargar();
}

var fAsesorChip = "";
var FINALES_JS = ["vendido","no_contesto","no_concretado","esperando_pago","cerrado"];

function pintarCarga(c){
  var el = document.getElementById("carga");
  if(!c){ el.innerHTML = ""; return; }
  var chips = Object.keys(c).map(function(a){
    var act = (fAsesorChip === a) ? ' style="background:#e30613;color:#fff;border-color:#e30613;"' : '';
    return '<div class="a" onclick="filtrarPorAsesor(\''+a+'\')"'+act+'>'+a+' <b>'+c[a]+'</b></div>';
  }).join("");
  el.innerHTML = '<div class="titulo">Clientes por atender (pendientes) — toca un nombre para ver los suyos:</div>' + chips;
}

function filtrarPorAsesor(a){
  fAsesorChip = (fAsesorChip === a) ? "" : a;  // toca otra vez para quitar
  aplicarBusqueda();
  if (window._cargaCache) pintarCarga(window._cargaCache);
}

function fmtDinero(n){
  n = Math.round((Number(n)||0));
  return "$" + n.toLocaleString("es-MX");
}

var MESES = ["Ene","Feb","Mar","Abr","May","Jun","Jul","Ago","Sep","Oct","Nov","Dic"];
function mesBonito(ym){
  var p = (ym||"").split("-");
  if (p.length < 2) return ym;
  return (MESES[parseInt(p[1],10)-1]||p[1]) + " " + p[0];
}
function nombreRango(rango){
  if (rango === "Todo") return "Todo";
  if (rango.indexOf(" a ") !== -1){
    var ab = rango.split(" a ");
    return mesBonito(ab[0]) + " a " + mesBonito(ab[1]);
  }
  return mesBonito(rango);
}

function pintarStats(s, ventas){
  if(!s) return;
  var ventaHtml = "";
  if (ventas){
    var etiqueta = "💰 Vendido" + (ventas.rango ? " · " + nombreRango(ventas.rango) : "");
    var hoyHtml = "";
    if (ventas.hoy !== undefined){
      var nh = ventas.hoy_num || 0;
      hoyHtml = '<div class="stat venta hoy"><b>'+fmtDinero(ventas.hoy)+'</b><span>☀️ Vendido hoy'+(nh?" ("+nh+")":"")+'</span></div>';
    }
    var acHtml = "";
    if (ventas.acumulado !== undefined){
      var na = ventas.acumulado_num || 0;
      acHtml = '<div class="stat venta total"><b>'+fmtDinero(ventas.acumulado)+'</b><span>🏆 Total acumulado'+(na?" ("+na+")":"")+'</span></div>';
    }
    ventaHtml = hoyHtml +
      '<div class="stat venta"><b>'+fmtDinero(ventas.total)+'</b><span>'+etiqueta+'</span></div>' +
      acHtml;
  }
  document.getElementById("stats").innerHTML =
    '<div class="stat"><b>'+(s.nuevo||0)+'</b><span>Nuevos</span></div>'+
    '<div class="stat"><b>'+(s.asignado||0)+'</b><span>Asignados</span></div>'+
    '<div class="stat"><b>'+(s.proceso||0)+'</b><span>En proceso</span></div>'+
    '<div class="stat"><b>'+(s.vendido||0)+'</b><span>Vendidos</span></div>'+
    '<div class="stat"><b>'+(s.esperando_pago||0)+'</b><span>💳 Esperando pago</span></div>'+
    '<div class="stat"><b>'+(s.no_contesto||0)+'</b><span>No contestó</span></div>'+
    '<div class="stat"><b>'+(s.no_concretado||0)+'</b><span>No concretados</span></div>'+
    ventaHtml;
}

function pintar(regs){
  var c = document.getElementById("lista");
  if (!regs || !regs.length){ c.innerHTML = '<div class="vacio">No hay registros con este filtro 👍</div>'; return; }
  var resumen = "";
  if (SOLO_CALIF){
    var nb=0,nr=0,nm=0;
    regs.forEach(function(r){ if(r.calificacion==="bueno")nb++; else if(r.calificacion==="regular")nr++; else if(r.calificacion==="malo")nm++; });
    resumen = '<div class="resumen-calif">'+
      '<span class="rc rc-b">😀 Bueno: <b>'+nb+'</b></span>'+
      '<span class="rc rc-r">😐 Regular: <b>'+nr+'</b></span>'+
      '<span class="rc rc-m">😞 Malo: <b>'+nm+'</b></span></div>';
  }
  c.innerHTML = resumen + regs.map(function(r){
    var wa = r.telefono ? r.telefono.replace(/\D/g,"") : "";
    var num10 = wa.slice(-10);  // número local de 10 dígitos (como lo ven en su WhatsApp)
    var numFmt = num10.replace(/(\d{3})(\d{3})(\d{4})/, "$1 $2 $3") || wa;
    var opcAsesor = ASESORES.map(function(a){ return '<option value="'+a+'"'+(a===r.asesor?' selected':'')+'>'+(a||'— Asignar —')+'</option>'; }).join("");
    var opcEstado = Object.keys(ESTADOS).map(function(e){ return '<option value="'+e+'"'+(e===r.estado?' selected':'')+'>'+ESTADOS[e]+'</option>'; }).join("");
    var alertaHtml = r.alerta ? '<div class="alerta-banner"><span>'+esc(r.alerta)+'</span><button onclick="resolverAlerta('+r.id+')">✓ Resuelto</button></div>' : '';
    var badgeExpres = r.expres ? '<span class="expres-badge">⚡ EXPRÉS</span>' : '';
    var badgeDiseno = r.diseno ? '<span class="diseno-badge">🎨 DISEÑO</span>' : '';
    var badgeAprob = r.diseno_aprobado ? '<span class="aprobado-badge">✅ APROBADO</span>' : '';
    var CALIF = {bueno:'😀 Bueno', regular:'😐 Regular', malo:'😞 Malo'};
    var badgeCalif = r.calificacion ? '<span class="calif-badge calif-'+r.calificacion+'">'+(CALIF[r.calificacion]||r.calificacion)+'</span>' : '';
    var badgeSuc = '<span class="suc-badge">🏢 '+(r.sucursal||"Campeche")+'</span>';
    var badgeFact = r.factura ? (r.facturado
        ? '<span class="fact-badge fact-ok">✅ Facturado</span>'
        : '<span class="fact-badge fact-pend">🧾 Por facturar</span>') : '';
    var opcSuc = ["Campeche","Mérida","Carmen"].map(function(su){ return '<option value="'+su+'"'+((r.sucursal||"Campeche")===su?' selected':'')+'>'+su+'</option>'; }).join("");
    var badgeEntrega = r.listo_entregar
        ? '<span class="entrega-badge">📦 Listo para entregar</span>'
        : (r.entrega_en ? '<span class="entrega-prog">📦 Entrega: '+r.entrega_en+'</span>' : '');
    return '<div class="card '+r.estado+(r.alerta?' alertado':'')+(r.expres?' expres-on':'')+'">'+
      '<div class="head"><span class="nombre">'+esc(r.nombre||"Sin nombre")+badgeExpres+badgeDiseno+badgeAprob+badgeCalif+badgeSuc+badgeFact+badgeEntrega+'</span>'+
      '<span class="tipo">'+(TIPOS[r.tipo]||r.tipo)+'</span></div>'+
      alertaHtml+
      '<div class="desc">'+esc(r.descripcion||"")+'</div>'+
      '<div class="meta">📅 '+(r.actualizado||r.creado)+'</div>'+
      '<div class="acciones">'+
        '<select onchange="upd('+r.id+',\'estado\',this.value)">'+opcEstado+'</select>'+
        '<select onchange="upd('+r.id+',\'asesor\',this.value)">'+opcAsesor+'</select>'+
        '<select onchange="upd('+r.id+',\'sucursal\',this.value)" title="Sucursal">'+opcSuc+'</select>'+
        '<button class="bexpres'+(r.expres?' on':'')+'" onclick="toggleExpres('+r.id+','+(r.expres?'false':'true')+')" title="Marcar/quitar exprés">⚡ Exprés</button>'+
        '<button class="bdiseno'+(r.diseno?' on':'')+'" onclick="toggleDiseno('+r.id+','+(r.diseno?'false':'true')+')" title="Marcar/quitar diseño">🎨 Diseño</button>'+
        '<button class="baprob'+(r.diseno_aprobado?' on':'')+'" onclick="toggleDisenoAprobado('+r.id+','+(r.diseno_aprobado?'false':'true')+')" title="Marcar/quitar diseño aprobado">✅ Aprobado</button>'+
        (r.estado==="proceso" ? '<button class="bentregar" onclick="if(confirm(\'¿Marcar como ENTREGADO? Pasa a Vendidos.\'))upd('+r.id+',\'estado\',\'vendido\')" title="Marcar pedido entregado">📦 Entregado</button>' : '')+
        (r.factura ? '<button class="bfact'+(r.facturado?' on':'')+'" onclick="toggleFacturado('+r.id+','+(r.facturado?'false':'true')+')" title="Marcar/quitar facturado">'+(r.facturado?'✅ Facturado':'🧾 Marcar facturado')+'</button>' : '')+
        (ES_DIRECTOR ? '<input class="monto" type="number" min="0" step="1" placeholder="$ monto" value="'+(r.monto?Math.round(r.monto):"")+'" title="Monto $ del pedido (para el total vendido)" onblur="upd('+r.id+',\'monto\',this.value)">' : '')+
        '<input class="nota" placeholder="Nota..." value="'+esc(r.notas||"")+'" onblur="upd('+r.id+',\'notas\',this.value)">'+
        (wa ? '<button class="copia" onclick="verChat(\''+wa+'\',\''+(r.nombre||"Cliente").replace(/[\\\\\x27"]/g,"")+'\')" title="Ver la conversación con Clio">👁️ Ver chat</button>' : '')+
        (num10 ? '<button class="copia" onclick="copiarNum(\''+num10+'\',this)" title="Copiar número para buscarlo en tu WhatsApp">📋 '+numFmt+'</button>'+
                 '<a class="wa" href="https://wa.me/'+wa+'" target="_blank" title="Abrir chat de WhatsApp">💬 Mandar</a>' : '')+
      '</div></div>';
  }).join("");
}

var chatTel = "", chatNombre = "";

async function verChat(tel, nombre){
  chatTel = tel; chatNombre = nombre;
  document.getElementById("modalNombre").textContent = "💬 " + (nombre || "Conversación");
  document.getElementById("modal").style.display = "flex";
  await cargarChat();
}

async function cargarChat(){
  var cuerpo = document.getElementById("modalChat");
  cuerpo.innerHTML = '<div class="vacio">Cargando conversación...</div>';
  try{
    var r = await fetch("/crm/api/chat?telefono=" + encodeURIComponent(chatTel), {headers:{"Authorization":"Bearer "+TOKEN}});
    var d = await r.json();
    pintarControl(d.control);
    if(!d.mensajes || !d.mensajes.length){ cuerpo.innerHTML = '<div class="vacio">Sin mensajes guardados de este cliente</div>'; return; }
    cuerpo.innerHTML = d.mensajes.map(function(m){
      var esAsesor = m.role === "assistant" && m.content.indexOf("[Asesor ") === 0;
      var cls = m.role === "user" ? "bcli" : (esAsesor ? "basesor" : "bclio");
      var quien, texto = m.content;
      if (m.role === "user"){ quien = "Cliente"; }
      else if (esAsesor){
        var mm = m.content.match(/^\[Asesor ([^\]]*)\]\s*/);
        quien = "✍️ " + (mm ? mm[1] : "Asesor");
        texto = m.content.replace(/^\[Asesor [^\]]*\]\s*/, "");
      } else { quien = "🤖 Clio"; }
      return '<div class="burb '+cls+'"><div class="quien">'+quien+' · '+m.hora+'</div>'+esc(texto)+'</div>';
    }).join("");
    cuerpo.scrollTop = cuerpo.scrollHeight;
  }catch(e){ cuerpo.innerHTML = '<div class="vacio">Error al cargar</div>'; }
}

async function abrirEquipo(){
  document.getElementById("modalEquipo").style.display = "flex";
  var cont = document.getElementById("equipoLista");
  cont.innerHTML = '<div class="vacio">Cargando...</div>';
  var r = await fetch("/crm/api/usuarios", {headers:{"Authorization":"Bearer "+TOKEN}});
  var d = await r.json();
  cont.innerHTML = (d.usuarios||[]).map(function(u){
    return '<div style="padding:12px;border-bottom:1px solid #eee;">'+
      '<div style="font-weight:bold;margin-bottom:6px;">'+esc(u.nombre)+' <span style="color:#999;font-weight:normal;">('+esc(u.usuario)+')</span></div>'+
      '<div style="display:flex;gap:6px;">'+
        '<input id="pwd_'+u.usuario+'" type="text" placeholder="Nueva contraseña" style="flex:1;padding:8px;border:1px solid #ccc;border-radius:6px;">'+
        '<button onclick="cambiarPwd(\''+u.usuario+'\')" style="background:#e30613;color:#fff;border:none;border-radius:6px;padding:8px 14px;cursor:pointer;font-weight:bold;">Cambiar</button>'+
      '</div></div>';
  }).join("");
}

function cerrarEquipo(){ document.getElementById("modalEquipo").style.display = "none"; }

async function cambiarPwd(usuario){
  var inp = document.getElementById("pwd_"+usuario);
  var nueva = inp.value.trim();
  if (nueva.length < 4){ alert("La contraseña debe tener al menos 4 caracteres"); return; }
  var r = await fetch("/crm/api/usuario/password", {method:"POST", headers:{"Authorization":"Bearer "+TOKEN, "Content-Type":"application/json"}, body: JSON.stringify({usuario: usuario, password: nueva})});
  var d = await r.json();
  if (d.ok){ inp.value = ""; alert("✅ Contraseña de "+usuario+" cambiada"); }
  else { alert("❌ "+(d.error||"No se pudo cambiar")); }
}

function pintarControl(c){
  var el = document.getElementById("modalCtrl");
  if (c && c.activo){
    el.innerHTML = '<span class="est">✋ Control: <b>'+esc(c.asesor||"Asesor")+'</b> (Clio en pausa)</span>'+
      '<button class="devolver" onclick="toggleControl(\'devolver\')">🤖 Devolver a Clio</button>';
  } else {
    el.innerHTML = '<span class="est">🤖 Clio está atendiendo</span>'+
      '<button class="tomar" onclick="toggleControl(\'tomar\')">✋ Tomar control</button>';
  }
}

async function toggleControl(accion){
  await fetch("/crm/api/control", {method:"POST", headers:{"Authorization":"Bearer "+TOKEN, "Content-Type":"application/json"}, body: JSON.stringify({telefono: chatTel, accion: accion})});
  await cargarChat();
}

async function enviarMensaje(){
  var inp = document.getElementById("msgInput");
  var msg = inp.value.trim();
  if (!msg) return;
  inp.value = "";
  await fetch("/crm/api/enviar", {method:"POST", headers:{"Authorization":"Bearer "+TOKEN, "Content-Type":"application/json"}, body: JSON.stringify({telefono: chatTel, mensaje: msg})});
  await cargarChat();
}

function cerrarModal(){ document.getElementById("modal").style.display = "none"; }

function copiarNum(num, btn){
  var orig = btn.innerHTML;
  function ok(){ btn.innerHTML = "✅ ¡Copiado!"; setTimeout(function(){ btn.innerHTML = orig; }, 1500); }
  if (navigator.clipboard && navigator.clipboard.writeText){
    navigator.clipboard.writeText(num).then(ok, function(){ prompt("Copia el número:", num); });
  } else {
    prompt("Copia el número:", num);
  }
}

async function upd(id, campo, valor){
  var body = {}; body[campo] = valor;
  await fetch("/crm/api/registro/"+id, {method:"POST", headers:{"Authorization":"Bearer "+TOKEN, "Content-Type":"application/json"}, body: JSON.stringify(body)});
  if (campo === "estado" || campo === "asesor" || campo === "monto" || campo === "sucursal") cargar();
}

function esc(s){ return (s||"").replace(/&/g,"&amp;").replace(/</g,"&lt;").replace(/>/g,"&gt;").replace(/"/g,"&quot;"); }
</script>
</body>
</html>
"""
