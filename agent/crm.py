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
  .stats { display: flex; gap: 10px; flex-wrap: wrap; margin-bottom: 14px; }
  .stat { background: #fff; border-radius: 10px; padding: 12px 18px; box-shadow: 0 1px 4px rgba(0,0,0,.05); }
  .stat b { font-size: 22px; display: block; color: #e30613; }
  .stat span { font-size: 12px; color: #777; }
  /* Tarjetas */
  .card { background: #fff; border-radius: 10px; padding: 14px; margin-bottom: 10px; box-shadow: 0 1px 4px rgba(0,0,0,.05); border-left: 4px solid #ccc; }
  .card.nuevo { border-left-color: #2196F3; }
  .card.asignado { border-left-color: #FF9800; }
  .card.proceso { border-left-color: #9C27B0; }
  .card.cerrado { border-left-color: #4CAF50; opacity: .7; }
  .card .head { display: flex; justify-content: space-between; align-items: start; flex-wrap: wrap; gap: 8px; }
  .card .nombre { font-weight: bold; font-size: 16px; }
  .card .tipo { font-size: 11px; padding: 2px 8px; border-radius: 10px; background: #eee; color: #555; text-transform: uppercase; }
  .card .desc { color: #444; font-size: 14px; margin: 8px 0; white-space: pre-wrap; }
  .card .meta { font-size: 12px; color: #999; }
  .card .acciones { display: flex; gap: 6px; flex-wrap: wrap; margin-top: 10px; align-items: center; }
  .card .acciones select, .card .acciones input { padding: 6px 8px; border: 1px solid #ddd; border-radius: 6px; font-size: 13px; }
  .card .acciones input.nota { flex: 1; min-width: 140px; }
  .card a.wa { background: #25D366; color: #fff; text-decoration: none; padding: 6px 12px; border-radius: 6px; font-size: 13px; }
  .vacio { text-align: center; color: #999; padding: 40px; }
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
    <div><span class="user" id="quien"></span> &nbsp; <button onclick="salir()">Salir</button></div>
  </div>
  <div class="wrap">
    <div class="carga" id="carga"></div>
    <div class="stats" id="stats"></div>
    <div class="filtros" id="filtros">
      <button data-f="estado" data-v="" class="activo">Todos</button>
      <button data-f="estado" data-v="nuevo">🔵 Nuevos</button>
      <button data-f="estado" data-v="asignado">🟠 Asignados</button>
      <button data-f="estado" data-v="proceso">🟣 En proceso</button>
      <button data-f="estado" data-v="cerrado">🟢 Cerrados</button>
    </div>
    <div class="filtros" id="filtros2">
      <button data-f="tipo" data-v="" class="activo">Todos los tipos</button>
      <button data-f="tipo" data-v="escalacion">Escalaciones</button>
      <button data-f="tipo" data-v="pedido">Pedidos</button>
      <button data-f="tipo" data-v="cliente">Clientes nuevos</button>
      <button data-f="tipo" data-v="ruleta">Ruleta</button>
    </div>
    <div id="lista"></div>
  </div>
</div>

<script>
var TOKEN = localStorage.getItem("crm_token") || "";
var fEstado = "", fTipo = "";
var ASESORES = ["", "Anna", "Brayan", "Tere", "Chino"];
var ESTADOS = {nuevo:"🔵 Nuevo", asignado:"🟠 Asignado", proceso:"🟣 En proceso", cerrado:"🟢 Cerrado"};
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
    pintarCarga(d.carga);
    pintarStats(d.stats);
    pintar(d.registros);
  }catch(e){ console.log(e); }
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
    '<div class="stat"><b>'+(s.cerrado||0)+'</b><span>Cerrados</span></div>';
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
    return '<div class="card '+r.estado+'">'+
      '<div class="head"><span class="nombre">'+esc(r.nombre||"Sin nombre")+'</span>'+
      '<span class="tipo">'+(TIPOS[r.tipo]||r.tipo)+'</span></div>'+
      '<div class="desc">'+esc(r.descripcion||"")+'</div>'+
      '<div class="meta">📅 '+r.creado+'</div>'+
      '<div class="acciones">'+
        '<select onchange="upd('+r.id+',\'estado\',this.value)">'+opcEstado+'</select>'+
        '<select onchange="upd('+r.id+',\'asesor\',this.value)">'+opcAsesor+'</select>'+
        '<input class="nota" placeholder="Nota..." value="'+esc(r.notas||"")+'" onblur="upd('+r.id+',\'notas\',this.value)">'+
        (num10 ? '<button class="wa" onclick="copiarNum(\''+num10+'\',this)" title="Copiar número y buscarlo en tu WhatsApp">📋 '+numFmt+'</button>' : '')+
      '</div></div>';
  }).join("");
}

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
