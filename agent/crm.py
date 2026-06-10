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
  .carga { display: flex; gap: 10px; flex-wrap: wrap; margin: 14px 0 6px; }
  .carga .a { background: #fff3e0; border: 1px solid #ffcc80; border-radius: 20px; padding: 8px 16px; font-size: 14px; }
  .carga .a b { color: #e65100; font-size: 17px; }
  .carga .titulo { width: 100%; font-size: 12px; color: #888; margin-bottom: 2px; }
  .buscador { width: 100%; padding: 11px 14px; border: 1px solid #ccc; border-radius: 10px; font-size: 15px; margin: 6px 0 12px; }
  .stats { display: flex; gap: 10px; flex-wrap: wrap; margin-bottom: 14px; }
  .stat { background: #fff; border-radius: 10px; padding: 12px 18px; box-shadow: 0 1px 4px rgba(0,0,0,.05); }
  .stat b { font-size: 22px; display: block; color: #e30613; }
  .stat span { font-size: 12px; color: #777; }
  /* Tarjetas */
  .card { background: #fff; border-radius: 10px; padding: 14px; margin-bottom: 10px; box-shadow: 0 1px 4px rgba(0,0,0,.05); border-left: 4px solid #ccc; }
  .card.nuevo { border-left-color: #2196F3; }
  .card.asignado { border-left-color: #FF9800; }
  .card.proceso { border-left-color: #9C27B0; }
  .card.vendido { border-left-color: #4CAF50; opacity: .75; }
  .card.no_contesto { border-left-color: #555; opacity: .65; }
  .card .head { display: flex; justify-content: space-between; align-items: start; flex-wrap: wrap; gap: 8px; }
  .card .nombre { font-weight: bold; font-size: 16px; }
  .card .tipo { font-size: 11px; padding: 2px 8px; border-radius: 10px; background: #eee; color: #555; text-transform: uppercase; }
  .card.alertado { border-left-color: #e30613; background: #fff6f6; }
  .card .alerta-banner { background: #ffe0e0; color: #b71c1c; border-radius: 8px; padding: 7px 10px; margin: 8px 0; font-size: 13px; font-weight: bold; display: flex; justify-content: space-between; align-items: center; gap: 8px; }
  .card .alerta-banner button { background: #b71c1c; color: #fff; border: none; border-radius: 6px; padding: 4px 10px; font-size: 12px; cursor: pointer; }
  .card .desc { color: #444; font-size: 14px; margin: 8px 0; white-space: pre-wrap; }
  .card .meta { font-size: 12px; color: #999; }
  .card .acciones { display: flex; gap: 6px; flex-wrap: wrap; margin-top: 10px; align-items: center; }
  .card .acciones select, .card .acciones input { padding: 6px 8px; border: 1px solid #ddd; border-radius: 6px; font-size: 13px; }
  .card .acciones input.nota { flex: 1; min-width: 140px; }
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
    <div><span class="user" id="quien"></span> &nbsp; <button id="btnEquipo" onclick="abrirEquipo()" style="display:none">👥 Equipo</button> &nbsp; <button onclick="salir()">Salir</button></div>
  </div>
  <div class="wrap">
    <div class="carga" id="carga"></div>
    <input id="buscar" class="buscador" placeholder="🔍 Buscar por nombre o número..." oninput="aplicarBusqueda()">
    <div class="stats" id="stats"></div>
    <div class="filtros" id="filtros">
      <button data-f="estado" data-v="" class="activo">Todos</button>
      <button data-f="estado" data-v="nuevo">🔵 Nuevos</button>
      <button data-f="estado" data-v="asignado">🟠 Asignados</button>
      <button data-f="estado" data-v="proceso">🟣 En proceso</button>
      <button data-f="estado" data-v="vendido">✅ Vendidos</button>
      <button data-f="estado" data-v="no_contesto">⚫ No contestó</button>
    </div>
    <div class="filtros" id="filtros2">
      <button data-f="tipo" data-v="" class="activo">Todos los tipos</button>
      <button data-f="tipo" data-v="escalacion">Escalaciones</button>
      <button data-f="tipo" data-v="pedido">Pedidos</button>
      <button data-f="tipo" data-v="cliente">Clientes nuevos</button>
      <button data-f="tipo" data-v="ruleta">Ruleta</button>
    </div>
    <div class="filtros">
      <button id="btnRevisar" onclick="toggleRevisar()" style="background:#ffe0e0;color:#b71c1c;border-color:#ffb3b3;">⚠️ Solo a revisar</button>
    </div>
    <div id="lista"></div>
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
var ASESORES = ["", "Anna", "Brayan", "Tere", "Chino"];
var ESTADOS = {nuevo:"🔵 Nuevo", asignado:"🟠 Asignado", proceso:"🟣 En proceso", vendido:"✅ Vendido", no_contesto:"⚫ No contestó"};
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
  cargar();
}

async function cargar(){
  try{
    var url = "/crm/api/registros?estado=" + encodeURIComponent(fEstado) + "&tipo=" + encodeURIComponent(fTipo);
    var r = await fetch(url, {headers:{"Authorization":"Bearer "+TOKEN}});
    if (r.status === 401){ salir(); return; }
    var d = await r.json();
    document.getElementById("quien").textContent = "👤 " + (d.nombre || "") + (d.es_director ? "" : " · viendo solo lo tuyo");
    document.getElementById("btnEquipo").style.display = d.es_director ? "inline-block" : "none";
    pintarCarga(d.carga);
    pintarStats(d.stats);
    ULTIMOS_REGISTROS = d.registros || [];
    aplicarBusqueda();
  }catch(e){ console.log(e); }
}

var ULTIMOS_REGISTROS = [];
var SOLO_REVISAR = false;
function aplicarBusqueda(){
  var q = (document.getElementById("buscar").value || "").toLowerCase().trim();
  var qDig = q.replace(/\D/g, "");
  var regs = ULTIMOS_REGISTROS;
  if (SOLO_REVISAR){ regs = regs.filter(function(r){ return r.alerta; }); }
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

function pintarCarga(c){
  var el = document.getElementById("carga");
  if(!c){ el.innerHTML = ""; return; }
  var chips = Object.keys(c).map(function(a){
    return '<div class="a">'+a+' <b>'+c[a]+'</b></div>';
  }).join("");
  el.innerHTML = '<div class="titulo">Clientes por atender (pendientes):</div>' + chips;
}

function pintarStats(s){
  if(!s) return;
  document.getElementById("stats").innerHTML =
    '<div class="stat"><b>'+(s.nuevo||0)+'</b><span>Nuevos</span></div>'+
    '<div class="stat"><b>'+(s.asignado||0)+'</b><span>Asignados</span></div>'+
    '<div class="stat"><b>'+(s.proceso||0)+'</b><span>En proceso</span></div>'+
    '<div class="stat"><b>'+(s.vendido||0)+'</b><span>Vendidos</span></div>'+
    '<div class="stat"><b>'+(s.no_contesto||0)+'</b><span>No contestó</span></div>';
}

function pintar(regs){
  var c = document.getElementById("lista");
  if (!regs || !regs.length){ c.innerHTML = '<div class="vacio">No hay registros con este filtro 👍</div>'; return; }
  c.innerHTML = regs.map(function(r){
    var wa = r.telefono ? r.telefono.replace(/\D/g,"") : "";
    var num10 = wa.slice(-10);  // número local de 10 dígitos (como lo ven en su WhatsApp)
    var numFmt = num10.replace(/(\d{3})(\d{3})(\d{4})/, "$1 $2 $3") || wa;
    var opcAsesor = ASESORES.map(function(a){ return '<option value="'+a+'"'+(a===r.asesor?' selected':'')+'>'+(a||'— Asignar —')+'</option>'; }).join("");
    var opcEstado = Object.keys(ESTADOS).map(function(e){ return '<option value="'+e+'"'+(e===r.estado?' selected':'')+'>'+ESTADOS[e]+'</option>'; }).join("");
    var alertaHtml = r.alerta ? '<div class="alerta-banner"><span>'+esc(r.alerta)+'</span><button onclick="resolverAlerta('+r.id+')">✓ Resuelto</button></div>' : '';
    return '<div class="card '+r.estado+(r.alerta?' alertado':'')+'">'+
      '<div class="head"><span class="nombre">'+esc(r.nombre||"Sin nombre")+'</span>'+
      '<span class="tipo">'+(TIPOS[r.tipo]||r.tipo)+'</span></div>'+
      alertaHtml+
      '<div class="desc">'+esc(r.descripcion||"")+'</div>'+
      '<div class="meta">📅 '+r.creado+'</div>'+
      '<div class="acciones">'+
        '<select onchange="upd('+r.id+',\'estado\',this.value)">'+opcEstado+'</select>'+
        '<select onchange="upd('+r.id+',\'asesor\',this.value)">'+opcAsesor+'</select>'+
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
  if (campo === "estado" || campo === "asesor") cargar();
}

function esc(s){ return (s||"").replace(/&/g,"&amp;").replace(/</g,"&lt;").replace(/>/g,"&gt;").replace(/"/g,"&quot;"); }
</script>
</body>
</html>
"""
