"""
web_server.py — Interfaz web para CrewAI con monitor de Gmail integrado.

Uso:
  uv run python web_server.py              # Arranca en http://localhost:8000
  uv run python web_server.py --port 9000  # Puerto personalizado
  uv run python web_server.py --no-gmail   # Sin monitor de Gmail
"""

import argparse
from html import escape as html_escape
import io
import json
import logging
import os
import sys
import threading
import time
from datetime import datetime
from pathlib import Path

# Forzar UTF-8 en stdout/stderr para evitar UnicodeEncodeError con emojis de CrewAI en Windows
if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

os.environ.setdefault("CREWAI_DISABLE_TELEMETRY", "true")
os.environ.setdefault("OTEL_SDK_DISABLED", "true")

from fastapi import FastAPI
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel

from ui_test_cases import UI_REQUIREMENT_CASES

sys.path.insert(0, str(Path(__file__).parent / "src"))

logging.basicConfig(level=logging.INFO, format="[%(asctime)s] %(levelname)s: %(message)s")
logger = logging.getLogger(__name__)

app = FastAPI(title="CrewAI Assistant", version="1.0.0")

# ─── Estado del monitor de Gmail ───────────────────────────────────────────

gmail_monitor_state = {
    "running": False,
    "thread": None,
    "emails_procesados": 0,
    "ultimo_check": None,
    "ultimo_error": None,
    "intervalo": 30,
    "logs": [],          # últimos N mensajes de log del monitor
    "events": [],        # notificaciones de la UI (confirmaciones, conflictos, etc.)
    "correos": [],       # inbox de correos procesados para la pestaña Correos
    "correos_sin_leer": 0,
}
_gmail_lock = threading.Lock()
_gmail_stop_event = threading.Event()

MAX_MONITOR_LOGS = 50
MAX_EVENTS = 30
MAX_CORREOS = 50


def _add_monitor_log(msg: str):
    """Añade un mensaje al log circular del monitor."""
    with _gmail_lock:
        gmail_monitor_state["logs"].append(
            {"ts": datetime.now().strftime("%H:%M:%S"), "msg": msg}
        )
        if len(gmail_monitor_state["logs"]) > MAX_MONITOR_LOGS:
            gmail_monitor_state["logs"] = gmail_monitor_state["logs"][-MAX_MONITOR_LOGS:]


def _add_event(tipo: str, titulo: str, detalle: str):
    """Añade una notificación visible en la UI."""
    with _gmail_lock:
        gmail_monitor_state["events"].append({
            "ts": datetime.now().strftime("%H:%M:%S"),
            "tipo": tipo,       # "confirmacion", "conflicto", "email", "error"
            "titulo": titulo,
            "detalle": detalle,
        })
        if len(gmail_monitor_state["events"]) > MAX_EVENTS:
            gmail_monitor_state["events"] = gmail_monitor_state["events"][-MAX_EVENTS:]


def _gmail_monitor_thread(intervalo: int):
    """Hilo que ejecuta el monitor de Gmail en background."""
    try:
        from gmail_monitor import get_google_services, get_new_emails, \
            get_unread_message_ids, procesar_email_con_crewai, \
            cargar_state, guardar_state, process_single_email_postactions

        _add_monitor_log("Conectando con Google APIs...")
        service, calendar_service = get_google_services()
        _add_monitor_log("Conexión establecida. Monitor activo.")

        with _gmail_lock:
            gmail_monitor_state["running"] = True

        state = cargar_state()
        if not state.get("baseline_initialized", False):
            baseline_ids = get_unread_message_ids(service)
            state["baseline_unread_ids"] = list(baseline_ids)
            state["baseline_initialized"] = True
            state["ultima_verificacion"] = datetime.now().isoformat()
            guardar_state(state)
            _add_monitor_log(f"Baseline: {len(baseline_ids)} emails previos ignorados")

        while not _gmail_stop_event.is_set():
            try:
                emails = get_new_emails(service, max_results=5)
                baseline_unread_ids = set(state.get("baseline_unread_ids", []))

                if emails:
                    for email in emails:
                        if _gmail_stop_event.is_set():
                            break
                        if email["message_id"] in state["ultimos_emails"]:
                            continue
                        if email["message_id"] in baseline_unread_ids:
                            continue

                        subject = email.get("subject", "Sin asunto")
                        sender = email.get("sender", "")
                        _add_monitor_log(f"Procesando: {subject} de {sender}")

                        resultado = procesar_email_con_crewai(email)
                        if resultado["success"]:
                            with _gmail_lock:
                                gmail_monitor_state["emails_procesados"] += 1
                                _store_correo(email, resultado)

                        # Delegar acciones post-análisis a función reutilizable
                        process_single_email_postactions(
                            service,
                            calendar_service,
                            email,
                            resultado,
                            test_mode=False,
                            log_fn=lambda msg: _add_monitor_log(f"  {msg}"),
                            event_fn=_add_event,
                        )

                        state["ultimos_emails"].append(email["message_id"])
                        state["ultimos_emails"] = state["ultimos_emails"][-100:]
                        state["ultima_verificacion"] = datetime.now().isoformat()
                        guardar_state(state)

                with _gmail_lock:
                    gmail_monitor_state["ultimo_check"] = datetime.now().isoformat()
                    gmail_monitor_state["ultimo_error"] = None

            except Exception as e:
                with _gmail_lock:
                    gmail_monitor_state["ultimo_error"] = str(e)
                _add_monitor_log(f"⚠ Error en ciclo: {e}")
                logger.error(f"Error en ciclo Gmail monitor: {e}")

            _gmail_stop_event.wait(timeout=intervalo)

    except Exception as e:
        _add_monitor_log(f"Error fatal: {e}")
        logger.error(f"Gmail monitor hilo error fatal: {e}")
        with _gmail_lock:
            gmail_monitor_state["ultimo_error"] = str(e)
    finally:
        with _gmail_lock:
            gmail_monitor_state["running"] = False
        _add_monitor_log("Monitor detenido.")


def start_gmail_monitor(intervalo: int = 30):
    """Arranca el monitor de Gmail en un hilo daemon."""
    with _gmail_lock:
        if gmail_monitor_state["running"]:
            return False
    _gmail_stop_event.clear()
    gmail_monitor_state["intervalo"] = intervalo
    t = threading.Thread(target=_gmail_monitor_thread, args=(intervalo,), daemon=True)
    t.start()
    gmail_monitor_state["thread"] = t
    return True


def stop_gmail_monitor():
    """Detiene el monitor de Gmail."""
    _gmail_stop_event.set()
    t = gmail_monitor_state.get("thread")
    if t and t.is_alive():
        t.join(timeout=5)
    with _gmail_lock:
        gmail_monitor_state["running"] = False


def _store_correo(email: dict, resultado: dict):
    """Almacena un email procesado en el inbox de la UI (llamar dentro de _gmail_lock)."""
    analisis = resultado.get("analisis_email", {}) if isinstance(resultado, dict) else {}
    clasificacion = resultado.get("clasificacion", {}) if isinstance(resultado, dict) else {}
    resumen_corto = str(clasificacion.get("resumen", "")).strip()
    if not resumen_corto:
        resumen_corto = str(resultado.get("resumen", ""))[:300].strip()
    correo_card = {
        "id": email.get("message_id", ""),
        "ts": datetime.now().strftime("%d/%m %H:%M"),
        "subject": email.get("subject", "Sin asunto"),
        "sender": email.get("sender", ""),
        "categoria": str(clasificacion.get("categoria", "")).strip(),
        "urgencia": str(analisis.get("urgencia", "no urgente")).strip(),
        "justificacion": str(analisis.get("justificacion_urgencia", "")).strip(),
        "resumen": resumen_corto,
        "posible_respuesta": str(resultado.get("resumen", "")).strip(),
        "acciones": analisis.get("acciones_pendientes", []) if isinstance(analisis, dict) else [],
        "leido": False,
    }
    gmail_monitor_state["correos"].insert(0, correo_card)
    gmail_monitor_state["correos"] = gmail_monitor_state["correos"][:MAX_CORREOS]
    gmail_monitor_state["correos_sin_leer"] += 1


# ─── Modelos de petición/respuesta ─────────────────────────────────────────

class PeticionRequest(BaseModel):
    peticion: str

class PeticionResponse(BaseModel):
    success: bool
    categoria: str = ""
    resultado: str = ""
    resumen: str = ""
    duracion_segundos: float = 0.0
    error: str = ""
    # Campos de calendario
    evento_creado: bool = False
    evento_conflicto: bool = False
    evento_fecha: str = ""
    evento_alternativas: list = []
    borrador_email: str = ""
    # RF5: resumen de agenda
    agenda_resumen: str = ""
    agenda_rango: str = ""

# ─── Endpoints API ─────────────────────────────────────────────────────────

@app.post("/api/peticion", response_model=PeticionResponse)
def procesar_peticion(req: PeticionRequest):
    """Recibe una petición de texto y la procesa con SupervisorFlow."""
    peticion = (req.peticion or "").strip()
    if not peticion:
        return PeticionResponse(success=False, error="La petición está vacía.")

    start_time = time.time()
    try:
        from src.oficinacrew.main import SupervisorFlow

        flow = SupervisorFlow()
        result = flow.kickoff(inputs={"peticion": peticion})
        clasificacion = flow.state.get("clasificacion", {})
        categoria = str(clasificacion.get("categoria", "")).strip().lower()

        elapsed = round(time.time() - start_time, 2)
        response = PeticionResponse(
            success=True,
            categoria=categoria,
            resultado=str(result),
            resumen=clasificacion.get("resumen", ""),
            duracion_segundos=elapsed,
        )

        # ── Integración Calendar para peticiones de agenda ──
        if categoria in {"agenda", "ambos"}:
            try:
                from gmail_monitor import has_explicit_scheduling_intent, has_agenda_summary_intent
                # Primero comprobar si es una CONSULTA de agenda (RF5), luego si es CREACIóN (RF4)
                # El orden importa: consultas como "¿qué citas tengo?"
                # no deben ir por el camino de crear eventos
                if has_agenda_summary_intent(peticion):
                    response = _handle_agenda_summary(peticion, response)
                elif has_explicit_scheduling_intent(peticion) or has_explicit_scheduling_intent(
                    str(clasificacion.get("texto_agenda", ""))
                ):
                    response = _handle_calendar_from_petition(peticion, clasificacion, response)
            except Exception as e:
                logger.error(f"Error en integración Calendar: {e}")
                _add_event("error", "Error Calendar", str(e))

        return response
    except Exception as e:
        elapsed = round(time.time() - start_time, 2)
        logger.error(f"Error procesando petición: {e}")
        return PeticionResponse(
            success=False,
            error=str(e),
            duracion_segundos=elapsed,
        )


def _handle_agenda_summary(peticion: str, response: PeticionResponse) -> PeticionResponse:
    """RF5: Obtiene eventos de Google Calendar en el rango solicitado y genera un resumen."""
    from gmail_monitor import (
        get_google_services,
        parse_date_range_for_summary,
        find_overlapping_events,
        build_agenda_summary_text,
    )

    reference_dt = datetime.now().astimezone()
    start_dt, end_dt = parse_date_range_for_summary(peticion, reference_dt)
    logger.info(f"[RF5] Resumen agenda: {start_dt.date()} → {end_dt.date()}")

    _, calendar_service = get_google_services()
    events = find_overlapping_events(calendar_service, start_dt, end_dt)
    summary_text = build_agenda_summary_text(events, start_dt, end_dt)

    response.agenda_resumen = summary_text
    response.agenda_rango = f"{start_dt.strftime('%d/%m/%Y')} - {end_dt.strftime('%d/%m/%Y')}"
    response.resultado = summary_text  # también en resultado para que lo muestre el flow

    _add_event(
        "email",
        f"Resumen de agenda",
        f"{len(events)} evento(s) entre {start_dt.strftime('%d/%m')} y {end_dt.strftime('%d/%m')}",
    )
    return response


def _handle_calendar_from_petition(
    peticion: str, clasificacion: dict, response: PeticionResponse
) -> PeticionResponse:
    """Comprueba disponibilidad y crea evento en Calendar desde una petición web."""
    from gmail_monitor import (
        get_google_services,
        has_explicit_scheduling_intent,
        _parse_event_datetimes_from_sources,
        find_overlapping_events,
        find_available_slots,
    )

    texto_agenda = str(clasificacion.get("texto_agenda", "")).strip()
    if not texto_agenda:
        texto_agenda = peticion

    logger.info(f"[DEBUG CALENDAR] peticion='{peticion}'")
    logger.info(f"[DEBUG CALENDAR] texto_agenda='{texto_agenda}'")
    logger.info(f"[DEBUG CALENDAR] has_scheduling_intent(texto_agenda)={has_explicit_scheduling_intent(texto_agenda)}")
    logger.info(f"[DEBUG CALENDAR] has_scheduling_intent(peticion)={has_explicit_scheduling_intent(peticion)}")

    if not has_explicit_scheduling_intent(texto_agenda) and not has_explicit_scheduling_intent(peticion):
        logger.info(f"[DEBUG CALENDAR] No scheduling intent detected, returning without calendar action")
        return response

    reference_dt = datetime.now().astimezone()
    sources = [peticion, texto_agenda]
    start_dt, end_dt = _parse_event_datetimes_from_sources(sources, reference_dt)
    logger.info(f"[DEBUG CALENDAR] parsed start_dt={start_dt}, end_dt={end_dt}")

    if not start_dt or not end_dt:
        logger.info(f"[DEBUG CALENDAR] No valid dates parsed, returning without calendar action")
        return response

    _, calendar_service = get_google_services()

    overlapping = find_overlapping_events(calendar_service, start_dt, end_dt)
    if overlapping:
        # ── Conflicto: proponer alternativas ──
        alternatives = find_available_slots(
            calendar_service, start_dt, end_dt,
            max_options=3, max_days_ahead=14,
        )
        alt_list = []
        alt_lines = []
        for idx, (a_start, a_end) in enumerate(alternatives, start=1):
            dur = int((a_end - a_start).total_seconds() // 60)
            alt_list.append({
                "opcion": idx,
                "inicio": a_start.strftime("%d/%m/%Y %H:%M"),
                "fin": a_end.strftime("%H:%M"),
                "duracion_min": dur,
            })
            alt_lines.append(
                f"  {idx}. {a_start.strftime('%d/%m/%Y a las %H:%M')} hasta "
                f"{a_end.strftime('%H:%M')} ({dur} min)"
            )

        conflict_names = ", ".join(
            ev.get("summary", "Sin título") for ev in overlapping[:3]
        )
        response.evento_conflicto = True
        response.evento_fecha = start_dt.strftime("%d/%m/%Y %H:%M")
        response.evento_alternativas = alt_list

        _add_event(
            "conflicto",
            f"Conflicto: {clasificacion.get('resumen', peticion[:60])}",
            f"Solapa con: {conflict_names}. Se sugieren {len(alt_list)} alternativas.",
        )

        # Borrador email con alternativas
        if alt_lines:
            response.borrador_email = (
                "Hola,\n\n"
                f"Quería proponerte una reunión pero tengo un conflicto en la franja "
                f"del {start_dt.strftime('%d/%m/%Y a las %H:%M')}.\n\n"
                "Te propongo estas opciones alternativas:\n"
                + "\n".join(alt_lines) + "\n\n"
                "Dime cuál te viene mejor y la dejo reservada.\n\n"
                "Un saludo."
            )
    else:
        # ── Sin conflicto: crear evento ──
        resumen_evento = clasificacion.get("resumen", peticion[:120])
        event_body = {
            "summary": resumen_evento,
            "description": f"Creado desde la interfaz web.\n\nPetición: {peticion}",
            "start": {"dateTime": start_dt.isoformat()},
            "end": {"dateTime": end_dt.isoformat()},
        }
        try:
            event = calendar_service.events().insert(
                calendarId="primary", body=event_body
            ).execute()
            response.evento_creado = True
            response.evento_fecha = start_dt.strftime("%d/%m/%Y %H:%M")

            _add_event(
                "confirmacion",
                f"Reunión creada: {resumen_evento}",
                f"{start_dt.strftime('%d/%m/%Y %H:%M')} - {end_dt.strftime('%H:%M')}",
            )

            # Borrador email de convocatoria
            response.borrador_email = (
                "Hola,\n\n"
                f"Te confirmo la reunión para el "
                f"{start_dt.strftime('%d/%m/%Y a las %H:%M')} "
                f"hasta las {end_dt.strftime('%H:%M')}.\n\n"
                f"Tema: {resumen_evento}\n\n"
                "Queda reservada en mi agenda.\n\n"
                "Un saludo."
            )
        except Exception as e:
            logger.error(f"Error creando evento en Calendar: {e}")
            _add_event("error", "Error creando evento", str(e))

    return response


@app.get("/api/gmail/status")
def gmail_status():
    """Estado actual del monitor de Gmail."""
    with _gmail_lock:
        return {
            "running": gmail_monitor_state["running"],
            "emails_procesados": gmail_monitor_state["emails_procesados"],
            "ultimo_check": gmail_monitor_state["ultimo_check"],
            "ultimo_error": gmail_monitor_state["ultimo_error"],
            "intervalo": gmail_monitor_state["intervalo"],
            "logs": gmail_monitor_state["logs"][-20:],
            "events": gmail_monitor_state["events"][-20:],
        }


@app.post("/api/gmail/start")
def gmail_start():
    """Arranca el monitor de Gmail."""
    ok = start_gmail_monitor(gmail_monitor_state["intervalo"])
    if ok:
        return {"success": True, "message": "Monitor de Gmail iniciado"}
    return {"success": False, "message": "El monitor ya está corriendo"}


@app.post("/api/gmail/stop")
def gmail_stop():
    """Detiene el monitor de Gmail."""
    stop_gmail_monitor()
    return {"success": True, "message": "Monitor de Gmail detenido"}


@app.get("/api/gmail/correos")
def gmail_correos():
    """Devuelve el inbox de correos procesados."""
    with _gmail_lock:
        return {
            "correos": gmail_monitor_state["correos"],
            "sin_leer": gmail_monitor_state["correos_sin_leer"],
        }


@app.post("/api/gmail/correos/leidos")
def gmail_marcar_leidos():
    """Marca todos los correos como leídos y resetea el contador."""
    with _gmail_lock:
        gmail_monitor_state["correos_sin_leer"] = 0
        for c in gmail_monitor_state["correos"]:
            c["leido"] = True
    return {"success": True}


def _build_ui_test_cases_html() -> str:
    """Renderiza una batería de pruebas manuales por requisito para la UI."""
    sections = []
    for requirement in UI_REQUIREMENT_CASES:
        cases_html = []
        for case in requirement.get("casos", []):
            prompt = str(case.get("prompt", "")).strip()
            expected = str(case.get("esperado", "")).strip()
            cases_html.append(
                """
                <div class="test-case-card">
                  <div class="test-case-prompt">{prompt}</div>
                  <div class="test-case-actions">
                    <button class="test-prompt-btn" data-prompt="{data_prompt}" onclick="usePrompt(this.dataset.prompt)">Usar en la UI</button>
                  </div>
                  <div class="test-case-expected"><strong>Esperado:</strong> {expected}</div>
                </div>
                """.format(
                    prompt=html_escape(prompt),
                    data_prompt=html_escape(prompt, quote=True),
                    expected=html_escape(expected),
                )
            )

        sections.append(
            """
            <section class="test-group">
              <div class="test-group-header">
                <span class="test-badge">{requisito}</span>
                <h3>{titulo}</h3>
              </div>
              <p class="test-group-objective">{objetivo}</p>
              <div class="test-case-list">{cases}</div>
            </section>
            """.format(
                requisito=html_escape(str(requirement.get("requisito", ""))),
                titulo=html_escape(str(requirement.get("titulo", ""))),
                objetivo=html_escape(str(requirement.get("objetivo", ""))),
                cases="".join(cases_html),
            )
        )

    return """
    <div class="manual-tests-launcher-wrap">
      <button class="manual-tests-launcher" onclick="openManualTests()">Abrir pruebas por requisito</button>
    </div>
    <div class="manual-tests-modal" id="manual-tests-modal" aria-hidden="true">
      <div class="manual-tests-backdrop" onclick="closeManualTests()"></div>
      <div class="manual-tests-window" role="dialog" aria-modal="true" aria-labelledby="manual-tests-title">
        <div class="manual-tests-window-header">
          <div>
            <h2 id="manual-tests-title">Pruebas por requisito</h2>
            <p>Casos manuales para validar desde la UI los requisitos completados y revisar el estado de los parciales.</p>
          </div>
          <button class="manual-tests-close" onclick="closeManualTests()" aria-label="Cerrar pruebas">Cerrar</button>
        </div>
        <div class="manual-tests-panel">
          <div class="test-groups">{sections}</div>
        </div>
      </div>
    </div>
    """.format(sections="".join(sections))

# ─── Interfaz HTML ─────────────────────────────────────────────────────────

HTML_PAGE = """<!DOCTYPE html>
<html lang="es">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>CrewAI Assistant</title>
<style>
  * { box-sizing: border-box; margin: 0; padding: 0; }
  body {
    font-family: 'Segoe UI', system-ui, -apple-system, sans-serif;
    background: #0f172a;
    color: #e2e8f0;
    min-height: 100vh;
    display: flex;
    flex-direction: column;
    align-items: center;
  }
  .container {
    width: 100%;
    max-width: 800px;
    padding: 2rem 1rem;
  }
  h1 {
    text-align: center;
    font-size: 1.8rem;
    margin-bottom: 0.3rem;
    color: #38bdf8;
  }
  .subtitle {
    text-align: center;
    color: #94a3b8;
    font-size: 0.9rem;
    margin-bottom: 1.5rem;
  }
  /* Gmail Monitor Panel */
  .gmail-panel {
    background: #1e293b;
    border: 1px solid #334155;
    border-radius: 10px;
    padding: 1rem 1.25rem;
    margin-bottom: 1.5rem;
  }
  .gmail-header {
    display: flex;
    align-items: center;
    justify-content: space-between;
    margin-bottom: 0.5rem;
  }
  .gmail-header h3 {
    font-size: 0.95rem;
    color: #e2e8f0;
    display: flex;
    align-items: center;
    gap: 0.5rem;
  }
  .status-dot {
    display: inline-block;
    width: 8px; height: 8px;
    border-radius: 50%;
    background: #ef4444;
  }
  .status-dot.active {
    background: #22c55e;
    box-shadow: 0 0 6px #22c55e88;
    animation: pulse-dot 2s infinite;
  }
  @keyframes pulse-dot {
    0%, 100% { opacity: 1; }
    50% { opacity: 0.5; }
  }
  .gmail-toggle {
    padding: 0.35rem 0.9rem;
    border: 1px solid #334155;
    border-radius: 6px;
    background: transparent;
    color: #94a3b8;
    font-size: 0.8rem;
    cursor: pointer;
    transition: all 0.2s;
  }
  .gmail-toggle:hover { border-color: #38bdf8; color: #38bdf8; }
  .gmail-toggle.stop { border-color: #ef4444; color: #ef4444; }
  .gmail-toggle.stop:hover { background: #ef444420; }
  .gmail-stats {
    display: flex;
    gap: 1.2rem;
    font-size: 0.8rem;
    color: #64748b;
    margin-bottom: 0.5rem;
  }
  .gmail-stats span { display: flex; align-items: center; gap: 0.3rem; }
  .gmail-stats strong { color: #94a3b8; }
  .gmail-logs {
    max-height: 0;
    overflow: hidden;
    transition: max-height 0.3s ease;
  }
  .gmail-logs.open { max-height: 200px; overflow-y: auto; }
  .gmail-logs-toggle {
    font-size: 0.75rem;
    color: #64748b;
    cursor: pointer;
    border: none;
    background: none;
    padding: 0.2rem 0;
  }
  .gmail-logs-toggle:hover { color: #94a3b8; }
  .gmail-log-entry {
    font-size: 0.75rem;
    color: #64748b;
    padding: 0.15rem 0;
    font-family: 'Consolas', monospace;
  }
  .gmail-log-entry .log-ts { color: #475569; margin-right: 0.5rem; }
  /* Input area */
  .input-area {
    display: flex;
    gap: 0.5rem;
    margin-bottom: 1.5rem;
  }
  textarea {
    flex: 1;
    padding: 0.75rem 1rem;
    border: 1px solid #334155;
    border-radius: 8px;
    background: #1e293b;
    color: #e2e8f0;
    font-size: 0.95rem;
    resize: vertical;
    min-height: 60px;
    font-family: inherit;
  }
  textarea:focus { outline: none; border-color: #38bdf8; }
  textarea::placeholder { color: #64748b; }
  button.btn-primary {
    padding: 0.75rem 1.5rem;
    border: none;
    border-radius: 8px;
    background: #2563eb;
    color: white;
    font-size: 0.95rem;
    font-weight: 600;
    cursor: pointer;
    white-space: nowrap;
    align-self: flex-end;
    transition: background 0.2s;
  }
  button.btn-primary:hover { background: #1d4ed8; }
  button.btn-primary:disabled { background: #475569; cursor: not-allowed; }
  .loader {
    display: none;
    text-align: center;
    padding: 2rem;
    color: #94a3b8;
  }
  .loader.active { display: block; }
  .loader .spinner {
    display: inline-block;
    width: 28px; height: 28px;
    border: 3px solid #334155;
    border-top-color: #38bdf8;
    border-radius: 50%;
    animation: spin 0.8s linear infinite;
    margin-bottom: 0.5rem;
  }
  @keyframes spin { to { transform: rotate(360deg); } }
  .result-card {
    background: #1e293b;
    border: 1px solid #334155;
    border-radius: 10px;
    padding: 1.25rem;
    margin-bottom: 1rem;
    animation: fadeIn 0.3s ease;
  }
  @keyframes fadeIn { from { opacity: 0; transform: translateY(8px); } to { opacity: 1; transform: translateY(0); } }
  .result-card.error { border-color: #ef4444; }
  .meta {
    display: flex;
    gap: 1rem;
    flex-wrap: wrap;
    margin-bottom: 0.75rem;
  }
  .badge {
    display: inline-block;
    padding: 0.2rem 0.6rem;
    border-radius: 4px;
    font-size: 0.8rem;
    font-weight: 600;
  }
  .badge.agenda { background: #065f46; color: #6ee7b7; }
  .badge.comunicacion { background: #1e3a5f; color: #7dd3fc; }
  .badge.documentos { background: #713f12; color: #fbbf24; }
  .badge.ambos { background: #581c87; color: #d8b4fe; }
  .badge.time { background: #334155; color: #94a3b8; }
  .result-text {
    white-space: pre-wrap;
    font-size: 0.9rem;
    line-height: 1.6;
    color: #cbd5e1;
  }
  .result-text strong { color: #f1f5f9; }
  /* Calendar info inside result cards */
  .calendar-info {
    margin-top: 0.75rem;
    padding: 0.75rem;
    border-radius: 6px;
    font-size: 0.85rem;
  }
  .calendar-info.created {
    background: #065f4620;
    border: 1px solid #065f46;
    color: #6ee7b7;
  }
  .calendar-info.conflict {
    background: #713f1220;
    border: 1px solid #713f12;
    color: #fbbf24;
  }
  .calendar-info h4 { margin-bottom: 0.4rem; font-size: 0.9rem; }
  .calendar-info .alt-list { padding-left: 1rem; margin: 0.3rem 0; }
  .calendar-info .alt-list li { margin: 0.2rem 0; }
  .email-draft {
    margin-top: 0.75rem;
    padding: 0.75rem;
    background: #0f172a;
    border: 1px solid #334155;
    border-radius: 6px;
    position: relative;
  }
  .email-draft-label {
    font-size: 0.75rem;
    color: #64748b;
    margin-bottom: 0.3rem;
    display: flex;
    justify-content: space-between;
    align-items: center;
  }
  .email-draft pre {
    white-space: pre-wrap;
    font-size: 0.82rem;
    color: #cbd5e1;
    font-family: inherit;
    margin: 0;
  }
  .copy-btn {
    padding: 0.2rem 0.5rem;
    border: 1px solid #475569;
    border-radius: 4px;
    background: transparent;
    color: #94a3b8;
    font-size: 0.7rem;
    cursor: pointer;
  }
  .copy-btn:hover { border-color: #38bdf8; color: #38bdf8; }
  .examples {
    display: flex;
    flex-wrap: wrap;
    gap: 0.4rem;
    margin-bottom: 1.5rem;
  }
  .example-chip {
    padding: 0.3rem 0.7rem;
    background: #1e293b;
    border: 1px solid #334155;
    border-radius: 16px;
    font-size: 0.8rem;
    color: #94a3b8;
    cursor: pointer;
    transition: all 0.2s;
  }
  .example-chip:hover { border-color: #38bdf8; color: #38bdf8; }
  .manual-tests-launcher-wrap {
    display: flex;
    justify-content: flex-end;
    margin-bottom: 1rem;
  }
  .manual-tests-launcher {
    padding: 0.55rem 0.95rem;
    border: 1px solid #0ea5e9;
    border-radius: 10px;
    background: #082f49;
    color: #e0f2fe;
    font-size: 0.84rem;
    font-weight: 600;
    cursor: pointer;
  }
  .manual-tests-launcher:hover {
    border-color: #38bdf8;
    background: #0c4a6e;
  }
  .manual-tests-modal {
    position: fixed;
    inset: 0;
    display: none;
    align-items: center;
    justify-content: center;
    z-index: 1100;
  }
  .manual-tests-modal.open {
    display: flex;
  }
  .manual-tests-backdrop {
    position: absolute;
    inset: 0;
    background: rgba(2, 6, 23, 0.7);
    backdrop-filter: blur(4px);
  }
  .manual-tests-window {
    position: relative;
    width: min(980px, calc(100vw - 2rem));
    max-height: min(88vh, 900px);
    overflow: hidden;
    background: #020617;
    border: 1px solid #334155;
    border-radius: 16px;
    box-shadow: 0 24px 60px rgba(2, 6, 23, 0.6);
    z-index: 1;
  }
  .manual-tests-window-header {
    display: flex;
    justify-content: space-between;
    gap: 1rem;
    align-items: flex-start;
    padding: 1rem 1.1rem;
    border-bottom: 1px solid #1e293b;
    background: #0b1220;
  }
  .manual-tests-window-header h2 {
    font-size: 1rem;
    color: #e2e8f0;
    margin-bottom: 0.25rem;
  }
  .manual-tests-window-header p {
    color: #94a3b8;
    font-size: 0.84rem;
  }
  .manual-tests-close {
    padding: 0.45rem 0.8rem;
    border: 1px solid #475569;
    border-radius: 8px;
    background: transparent;
    color: #cbd5e1;
    font-size: 0.8rem;
    cursor: pointer;
  }
  .manual-tests-close:hover {
    border-color: #94a3b8;
    color: #f8fafc;
  }
  .manual-tests-panel {
    background: #111827;
    padding: 1rem 1.1rem;
    max-height: calc(88vh - 84px);
    overflow-y: auto;
  }
  .test-groups {
    display: grid;
    grid-template-columns: 1fr;
    gap: 0.85rem;
  }
  .test-group {
    background: #0f172a;
    border: 1px solid #1e293b;
    border-radius: 10px;
    padding: 0.9rem;
  }
  .test-group-header {
    display: flex;
    gap: 0.6rem;
    align-items: center;
    margin-bottom: 0.45rem;
    flex-wrap: wrap;
  }
  .test-group-header h3 {
    font-size: 0.92rem;
    color: #e2e8f0;
  }
  .test-badge {
    display: inline-flex;
    align-items: center;
    justify-content: center;
    min-width: 48px;
    padding: 0.2rem 0.45rem;
    border-radius: 999px;
    border: 1px solid #1d4ed8;
    background: #0b3b84;
    color: #dbeafe;
    font-size: 0.72rem;
    font-weight: 700;
  }
  .test-group-objective {
    color: #94a3b8;
    font-size: 0.82rem;
    margin-bottom: 0.7rem;
  }
  .test-case-list {
    display: grid;
    gap: 0.6rem;
  }
  .test-case-card {
    border: 1px solid #334155;
    background: #111827;
    border-radius: 8px;
    padding: 0.75rem;
  }
  .test-case-prompt {
    color: #e2e8f0;
    font-size: 0.84rem;
    margin-bottom: 0.5rem;
    line-height: 1.45;
  }
  .test-case-actions {
    margin-bottom: 0.45rem;
  }
  .test-prompt-btn {
    padding: 0.35rem 0.7rem;
    border: 1px solid #0ea5e9;
    border-radius: 8px;
    background: #082f49;
    color: #e0f2fe;
    font-size: 0.78rem;
    cursor: pointer;
  }
  .test-prompt-btn:hover {
    border-color: #38bdf8;
    background: #0c4a6e;
  }
  .test-case-expected {
    color: #94a3b8;
    font-size: 0.79rem;
    line-height: 1.4;
  }
  /* Notifications panel */
  .notif-panel {
    position: fixed;
    top: 1rem;
    right: 1rem;
    width: min(360px, calc(100vw - 2rem));
    max-height: 55vh;
    background: #0b1220;
    border: 1px solid #334155;
    border-radius: 10px;
    padding: 0.75rem;
    z-index: 1000;
    box-shadow: 0 12px 30px rgba(2, 6, 23, 0.55);
  }
  .notif-panel h3 {
    font-size: 0.85rem;
    color: #94a3b8;
    margin-bottom: 0.6rem;
  }
  #notif-list {
    max-height: calc(55vh - 2.2rem);
    overflow-y: auto;
    padding-right: 0.2rem;
  }
  #notif-list::-webkit-scrollbar {
    width: 8px;
  }
  #notif-list::-webkit-scrollbar-thumb {
    background: #334155;
    border-radius: 8px;
  }
  #notif-list::-webkit-scrollbar-track {
    background: #0f172a;
  }
  .notif-card {
    display: flex;
    gap: 0.75rem;
    align-items: flex-start;
    background: #1e293b;
    border: 1px solid #334155;
    border-radius: 8px;
    padding: 0.7rem 1rem;
    margin-bottom: 0.5rem;
    animation: fadeIn 0.3s ease;
  }
  .notif-card.confirmacion { border-left: 3px solid #22c55e; }
  .notif-card.conflicto { border-left: 3px solid #f59e0b; }
  .notif-card.email { border-left: 3px solid #38bdf8; }
  .notif-card.error { border-left: 3px solid #ef4444; }
  .notif-icon { font-size: 1.1rem; flex-shrink: 0; margin-top: 0.1rem; }
  .notif-body { flex: 1; min-width: 0; }
  .notif-title { font-size: 0.85rem; font-weight: 600; color: #e2e8f0; }
  .notif-detail { font-size: 0.78rem; color: #94a3b8; margin-top: 0.15rem; }
  .notif-ts { font-size: 0.7rem; color: #475569; white-space: nowrap; flex-shrink: 0; }
  @media (max-width: 900px) {
    .notif-panel {
      top: 0.75rem;
      right: 0.75rem;
      max-height: 45vh;
    }
    #notif-list {
      max-height: calc(45vh - 2.2rem);
    }
    .manual-tests-window {
      width: calc(100vw - 1rem);
      max-height: 92vh;
    }
    .manual-tests-window-header {
      flex-direction: column;
      align-items: stretch;
    }
  }
  /* ── Tabs ── */
  .tabs-nav {
    display: flex;
    gap: 0.25rem;
    border-bottom: 1px solid #334155;
    margin-bottom: 1.25rem;
  }
  .tab-btn {
    padding: 0.55rem 1.1rem;
    border: none;
    border-bottom: 2px solid transparent;
    margin-bottom: -1px;
    background: transparent;
    color: #64748b;
    font-size: 0.9rem;
    font-weight: 500;
    cursor: pointer;
    transition: all 0.2s;
    display: flex;
    align-items: center;
    gap: 0.4rem;
  }
  .tab-btn:hover { color: #94a3b8; }
  .tab-btn.active { color: #38bdf8; border-bottom-color: #38bdf8; }
  .tab-badge {
    display: inline-flex;
    align-items: center;
    justify-content: center;
    min-width: 18px;
    height: 18px;
    padding: 0 5px;
    border-radius: 999px;
    background: #ef4444;
    color: #fff;
    font-size: 0.68rem;
    font-weight: 700;
  }
  .tab-panel { display: none; }
  .tab-panel.active { display: block; }
  /* ── Email cards ── */
  .correos-toolbar {
    display: flex;
    justify-content: flex-end;
    margin-bottom: 0.75rem;
  }
  .correos-mark-read-btn {
    padding: 0.35rem 0.8rem;
    border: 1px solid #334155;
    border-radius: 6px;
    background: transparent;
    color: #64748b;
    font-size: 0.78rem;
    cursor: pointer;
  }
  .correos-mark-read-btn:hover { border-color: #475569; color: #94a3b8; }
  .correos-empty {
    text-align: center;
    color: #475569;
    padding: 3rem 1rem;
    font-size: 0.9rem;
    line-height: 1.7;
  }
  .correo-card {
    background: #1e293b;
    border: 1px solid #334155;
    border-radius: 10px;
    padding: 1rem 1.25rem;
    margin-bottom: 0.85rem;
    animation: fadeIn 0.3s ease;
  }
  .correo-card.no-leido { border-left: 3px solid #38bdf8; }
  .correo-card-header {
    display: flex;
    justify-content: space-between;
    align-items: flex-start;
    gap: 0.5rem;
    margin-bottom: 0.55rem;
    flex-wrap: wrap;
  }
  .correo-subject { font-size: 0.95rem; font-weight: 600; color: #e2e8f0; flex: 1; }
  .correo-sender { font-size: 0.78rem; color: #64748b; margin-top: 0.12rem; }
  .correo-ts { font-size: 0.72rem; color: #475569; white-space: nowrap; flex-shrink: 0; }
  .correo-badges { display: flex; gap: 0.4rem; flex-wrap: wrap; margin-bottom: 0.55rem; }
  .urg-urgente { background: #450a0a; color: #fca5a5; }
  .urg-no-urgente { background: #082f49; color: #7dd3fc; }
  .urg-trivial { background: #1c1917; color: #a8a29e; }
  .correo-section {
    margin-top: 0.6rem;
    padding-top: 0.6rem;
    border-top: 1px solid #1e293b;
  }
  .correo-section-label {
    font-size: 0.7rem;
    font-weight: 700;
    text-transform: uppercase;
    letter-spacing: 0.06em;
    color: #64748b;
    margin-bottom: 0.3rem;
  }
  .correo-section-body {
    font-size: 0.85rem;
    color: #cbd5e1;
    line-height: 1.55;
    white-space: pre-wrap;
  }
  .correo-actions-list { list-style: none; padding: 0; margin: 0; }
  .correo-actions-list li {
    display: flex;
    gap: 0.5rem;
    align-items: flex-start;
    padding: 0.25rem 0;
    font-size: 0.83rem;
    color: #cbd5e1;
    line-height: 1.4;
  }
  .correo-actions-list li::before { content: '›'; color: #38bdf8; font-weight: 700; flex-shrink: 0; }
  .correo-prio-alta { color: #fca5a5; font-weight: 600; }
  .correo-prio-media { color: #fbbf24; }
  .correo-expandable summary {
    cursor: pointer;
    color: #64748b;
    font-size: 0.78rem;
    padding: 0.25rem 0;
    list-style: none;
    user-select: none;
  }
  .correo-expandable summary:hover { color: #94a3b8; }
  .correo-expandable[open] summary { color: #94a3b8; }
</style>
</head>
<body>
<div class="container">
  <h1>CrewAI Assistant</h1>
  <p class="subtitle">Sistema multi-agente: agenda, comunicación y documentos</p>

  <!-- Gmail Monitor Panel -->
  <div class="gmail-panel">
    <div class="gmail-header">
      <h3><span class="status-dot" id="gmail-dot"></span> Monitor Gmail</h3>
      <button class="gmail-toggle" id="gmail-btn" onclick="toggleGmail()">Iniciar</button>
    </div>
    <div class="gmail-stats" id="gmail-stats">
      <span>Estado: <strong id="gmail-estado">Detenido</strong></span>
      <span>Emails: <strong id="gmail-count">0</strong></span>
      <span>Último check: <strong id="gmail-last">—</strong></span>
    </div>
    <button class="gmail-logs-toggle" onclick="toggleLogs()">▸ Ver logs</button>
    <div class="gmail-logs" id="gmail-logs"></div>
  </div>
  <!-- Notifications -->
  <div class="notif-panel" id="notif-panel" style="display:none">
    <h3>Actividad del Monitor</h3>
    <div id="notif-list"></div>
  </div>

  <!-- Tabs -->
  <nav class="tabs-nav">
    <button class="tab-btn active" id="tab-asistente-btn" onclick="switchTab('asistente')">Asistente</button>
    <button class="tab-btn" id="tab-correos-btn" onclick="switchTab('correos')">
      Correos <span class="tab-badge" id="correos-badge" style="display:none">0</span>
    </button>
  </nav>

  <!-- Panel: Asistente -->
  <div class="tab-panel active" id="panel-asistente">
    <div class="examples">
      <span class="example-chip" onclick="useExample(this)">Organiza una reunión mañana a las 10</span>
      <span class="example-chip" onclick="useExample(this)">Redacta un email al cliente sobre el retraso del proyecto</span>
      <span class="example-chip" onclick="useExample(this)">Busca documentos sobre vacaciones</span>
      <span class="example-chip" onclick="useExample(this)">Resume el documento politica_teletrabajo.md</span>
    </div>

    __UI_TEST_CASES__

    <div class="input-area">
      <textarea id="peticion" placeholder="Escribe tu petición aquí..." rows="2"></textarea>
      <button class="btn-primary" id="btn-enviar" onclick="enviar()">Enviar</button>
    </div>

    <div class="loader" id="loader">
      <div class="spinner"></div>
      <div>Procesando con CrewAI...</div>
    </div>

    <div id="resultados"></div>
  </div>

  <!-- Panel: Correos -->
  <div class="tab-panel" id="panel-correos">
    <div class="correos-toolbar">
      <button class="correos-mark-read-btn" onclick="marcarCorreosLeidos()">Marcar todo como leído</button>
    </div>
    <div id="correos-list">
      <div class="correos-empty">No hay correos procesados aún.<br>Activa el monitor de Gmail para empezar a recibir.</div>
    </div>
  </div>
</div>

<script>
const input = document.getElementById('peticion');
const btn = document.getElementById('btn-enviar');
const loader = document.getElementById('loader');
const resultados = document.getElementById('resultados');

// ── Gmail Monitor ──
let gmailPolling = null;

async function fetchGmailStatus() {
  try {
    const r = await fetch('/api/gmail/status');
    const d = await r.json();
    const dot = document.getElementById('gmail-dot');
    const estado = document.getElementById('gmail-estado');
    const count = document.getElementById('gmail-count');
    const last = document.getElementById('gmail-last');
    const gbtn = document.getElementById('gmail-btn');

    dot.className = 'status-dot' + (d.running ? ' active' : '');
    estado.textContent = d.running ? 'Activo' : (d.ultimo_error ? 'Error' : 'Detenido');
    count.textContent = d.emails_procesados;
    last.textContent = d.ultimo_check ? new Date(d.ultimo_check).toLocaleTimeString('es') : '—';

    gbtn.textContent = d.running ? 'Detener' : 'Iniciar';
    gbtn.className = 'gmail-toggle' + (d.running ? ' stop' : '');

    // Logs
    const logsDiv = document.getElementById('gmail-logs');
    if (d.logs && d.logs.length) {
      logsDiv.innerHTML = d.logs.map(l =>
        `<div class="gmail-log-entry"><span class="log-ts">${l.ts}</span>${escapeHtml(l.msg)}</div>`
      ).join('');
      logsDiv.scrollTop = logsDiv.scrollHeight;
    }

    // Events / Notifications
    renderEvents(d.events || []);
  } catch(e) {}
}

async function toggleGmail() {
  const gbtn = document.getElementById('gmail-btn');
  const isRunning = gbtn.textContent === 'Detener';
  gbtn.disabled = true;
  try {
    await fetch('/api/gmail/' + (isRunning ? 'stop' : 'start'), { method: 'POST' });
    setTimeout(fetchGmailStatus, 500);
  } finally {
    gbtn.disabled = false;
  }
}

function toggleLogs() {
  const logs = document.getElementById('gmail-logs');
  const btn = logs.previousElementSibling;
  logs.classList.toggle('open');
  btn.textContent = logs.classList.contains('open') ? '▾ Ocultar logs' : '▸ Ver logs';
}

// Poll status cada 5s
fetchGmailStatus();
gmailPolling = setInterval(fetchGmailStatus, 5000);

// ── Peticiones ──
input.addEventListener('keydown', e => {
  if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); enviar(); }
});

function useExample(el) {
  input.value = el.textContent;
  input.focus();
}

function usePrompt(prompt) {
  input.value = prompt;
  closeManualTests();
  input.focus();
}

function openManualTests() {
  const modal = document.getElementById('manual-tests-modal');
  if (!modal) return;
  modal.classList.add('open');
  modal.setAttribute('aria-hidden', 'false');
  document.body.style.overflow = 'hidden';
}

function closeManualTests() {
  const modal = document.getElementById('manual-tests-modal');
  if (!modal) return;
  modal.classList.remove('open');
  modal.setAttribute('aria-hidden', 'true');
  document.body.style.overflow = '';
}

document.addEventListener('keydown', e => {
  if (e.key === 'Escape') {
    closeManualTests();
  }
});

async function enviar() {
  const peticion = input.value.trim();
  if (!peticion) return;

  btn.disabled = true;
  loader.classList.add('active');

  try {
    const resp = await fetch('/api/peticion', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ peticion }),
    });
    const data = await resp.json();
    mostrarResultado(peticion, data);
    input.value = '';
  } catch (err) {
    mostrarResultado(peticion, { success: false, error: err.message });
  } finally {
    btn.disabled = false;
    loader.classList.remove('active');
  }
}

function mostrarResultado(peticion, data) {
  const card = document.createElement('div');
  card.className = 'result-card' + (data.success ? '' : ' error');

  let meta = '';
  if (data.success && data.categoria) {
    meta += `<span class="badge ${data.categoria}">${data.categoria}</span>`;
  }
  if (data.evento_creado) {
    meta += `<span class="badge agenda">evento creado</span>`;
  }
  if (data.agenda_resumen) {
    meta += `<span class="badge agenda">resumen agenda</span>`;
  }
  if (data.evento_conflicto) {
    meta += `<span class="badge" style="background:#713f12;color:#fbbf24">conflicto</span>`;
  }
  if (data.duracion_segundos) {
    meta += `<span class="badge time">${data.duracion_segundos}s</span>`;
  }

  let body = '';
  if (data.success) {
    body = data.resultado || data.resumen || 'Sin resultado';
  } else {
    body = `Error: ${data.error || 'Error desconocido'}`;
  }

  let calendarHtml = '';
  if (data.evento_creado) {
    calendarHtml = `
      <div class="calendar-info created">
        <h4>Evento creado en Calendar</h4>
        Fecha: ${escapeHtml(data.evento_fecha)}
      </div>`;
  } else if (data.evento_conflicto) {
    let altHtml = '';
    if (data.evento_alternativas && data.evento_alternativas.length) {
      altHtml = '<ul class="alt-list">' + data.evento_alternativas.map(a =>
        `<li>Opción ${a.opcion}: ${escapeHtml(a.inicio)} hasta ${escapeHtml(a.fin)} (${a.duracion_min} min)</li>`
      ).join('') + '</ul>';
    }
    calendarHtml = `
      <div class="calendar-info conflict">
        <h4>Conflicto de agenda</h4>
        La franja solicitada (${escapeHtml(data.evento_fecha)}) no está disponible.
        ${altHtml ? '<br>Alternativas disponibles:' + altHtml : '<br>No se encontraron alternativas.'}
      </div>`;
  }

  let draftHtml = '';
  if (data.borrador_email) {
    const draftId = 'draft-' + Date.now();
    draftHtml = `
      <div class="email-draft">
        <div class="email-draft-label">
          <span>Borrador de email</span>
          <button class="copy-btn" onclick="copyDraft('${draftId}')">Copiar</button>
        </div>
        <pre id="${draftId}">${escapeHtml(data.borrador_email)}</pre>
      </div>`;
  }

  let agendaHtml = '';
  if (data.agenda_resumen) {
    const hasConflict = data.agenda_resumen.includes('\u26a0');
    agendaHtml = `
      <div class="calendar-info ${hasConflict ? 'conflict' : 'created'}" style="margin-top:12px">
        <h4>Resumen de agenda${data.agenda_rango ? ' (' + escapeHtml(data.agenda_rango) + ')' : ''}</h4>
        <pre style="white-space:pre-wrap;font-size:0.88rem;margin:8px 0 0">${escapeHtml(data.agenda_resumen)}</pre>
      </div>`;
  }

  card.innerHTML = `
    <div class="meta">${meta}</div>
    <div class="result-text"><strong>Petición:</strong> ${escapeHtml(peticion)}\\n\\n${escapeHtml(body)}</div>
    ${calendarHtml}
    ${agendaHtml}
    ${draftHtml}
  `;

  resultados.insertBefore(card, resultados.firstChild);
}

function copyDraft(id) {
  const pre = document.getElementById(id);
  if (pre) {
    navigator.clipboard.writeText(pre.textContent).then(() => {
      const btn = pre.parentElement.querySelector('.copy-btn');
      btn.textContent = 'Copiado';
      setTimeout(() => btn.textContent = 'Copiar', 2000);
    });
  }
}

function escapeHtml(text) {
  const div = document.createElement('div');
  div.textContent = text;
  return div.innerHTML;
}

const NOTIF_ICONS = {
  confirmacion: '\\u2705',
  conflicto: '\\u26a0\\ufe0f',
  email: '\\ud83d\\udce8',
  error: '\\u274c'
};
let lastEventsCount = 0;

function renderEvents(events) {
  const panel = document.getElementById('notif-panel');
  const list = document.getElementById('notif-list');
  if (!events.length) { panel.style.display = 'none'; return; }
  panel.style.display = 'block';

  // Solo re-renderizar si hay nuevos
  if (events.length === lastEventsCount) return;
  lastEventsCount = events.length;

  list.innerHTML = events.slice().reverse().map(ev => `
    <div class="notif-card ${ev.tipo}">
      <span class="notif-icon">${NOTIF_ICONS[ev.tipo] || '\\ud83d\\udce8'}</span>
      <div class="notif-body">
        <div class="notif-title">${escapeHtml(ev.titulo)}</div>
        <div class="notif-detail">${escapeHtml(ev.detalle)}</div>
      </div>
      <span class="notif-ts">${ev.ts}</span>
    </div>
  `).join('');
}

// ── Correos Tab ──
let lastCorreosCount = -1;

async function fetchCorreos() {
  try {
    const r = await fetch('/api/gmail/correos');
    const d = await r.json();
    const correos = d.correos || [];
    const sinLeer = d.sin_leer || 0;

    // Badge en la pestaña
    const badge = document.getElementById('correos-badge');
    if (sinLeer > 0) {
      badge.textContent = sinLeer;
      badge.style.display = 'inline-flex';
    } else {
      badge.style.display = 'none';
    }

    // Solo re-renderizar si hay cambios
    if (correos.length === lastCorreosCount) return;
    lastCorreosCount = correos.length;
    renderCorreos(correos);
  } catch(e) {}
}

function renderCorreos(correos) {
  const list = document.getElementById('correos-list');
  if (!correos.length) {
    list.innerHTML = '<div class="correos-empty">No hay correos procesados a\\u00fan.<br>Activa el monitor de Gmail para empezar a recibir.</div>';
    return;
  }
  list.innerHTML = correos.map(c => renderCorreoCard(c)).join('');
}

function renderCorreoCard(c) {
  const urgClass = c.urgencia === 'urgente' ? 'urg-urgente' : (c.urgencia === 'trivial' ? 'urg-trivial' : 'urg-no-urgente');
  const noLeidoClass = c.leido ? '' : ' no-leido';

  let accionesHtml = '';
  if (c.acciones && c.acciones.length) {
    const items = c.acciones.map(a => {
      const prioClass = a.prioridad === 'alta' ? 'correo-prio-alta' : (a.prioridad === 'media' ? 'correo-prio-media' : '');
      return `<li>${escapeHtml(a.accion || '')}${a.fecha_limite ? ` <span style="color:#64748b">\\u00b7 ${escapeHtml(a.fecha_limite)}</span>` : ''} <span class="${prioClass}">[${escapeHtml(a.prioridad || 'media')}]</span></li>`;
    }).join('');
    accionesHtml = `
      <div class="correo-section">
        <div class="correo-section-label">Acciones pendientes (${c.acciones.length})</div>
        <ul class="correo-actions-list">${items}</ul>
      </div>`;
  }

  let respuestaHtml = '';
  if (c.posible_respuesta && c.posible_respuesta.length > 10) {
    respuestaHtml = `
      <div class="correo-section">
        <details class="correo-expandable">
          <summary>Ver posible respuesta \\u2192</summary>
          <div class="correo-section-body" style="margin-top:0.4rem">${escapeHtml(c.posible_respuesta)}</div>
        </details>
      </div>`;
  }

  return `
    <div class="correo-card${noLeidoClass}">
      <div class="correo-card-header">
        <div style="flex:1;min-width:0">
          <div class="correo-subject">${escapeHtml(c.subject)}</div>
          <div class="correo-sender">${escapeHtml(c.sender)}</div>
        </div>
        <span class="correo-ts">${c.ts}</span>
      </div>
      <div class="correo-badges">
        ${c.categoria ? `<span class="badge ${escapeHtml(c.categoria)}">${escapeHtml(c.categoria)}</span>` : ''}
        <span class="badge ${urgClass}">${escapeHtml(c.urgencia)}</span>
      </div>
      ${c.resumen ? `<div class="correo-section"><div class="correo-section-label">Resumen</div><div class="correo-section-body">${escapeHtml(c.resumen)}</div></div>` : ''}
      ${accionesHtml}
      ${respuestaHtml}
    </div>`;
}

async function marcarCorreosLeidos() {
  try {
    await fetch('/api/gmail/correos/leidos', { method: 'POST' });
    const badge = document.getElementById('correos-badge');
    badge.style.display = 'none';
    lastCorreosCount = -1;  // Forzar re-render para marcar cards como leídas
    fetchCorreos();
  } catch(e) {}
}

function switchTab(tab) {
  ['asistente', 'correos'].forEach(t => {
    document.getElementById(`panel-${t}`).classList.toggle('active', t === tab);
    document.getElementById(`tab-${t}-btn`).classList.toggle('active', t === tab);
  });
  if (tab === 'correos') marcarCorreosLeidos();
}

// Poll correos cada 10s
fetchCorreos();
setInterval(fetchCorreos, 10000);
</script>
</body>
</html>""".replace("__UI_TEST_CASES__", _build_ui_test_cases_html())


@app.get("/", response_class=HTMLResponse)
def index():
    """Sirve la interfaz web."""
    return HTML_PAGE


# ─── CLI ───────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Interfaz web para CrewAI Assistant")
    parser.add_argument("--host", default="127.0.0.1", help="Host (default: 127.0.0.1)")
    parser.add_argument("--port", type=int, default=8000, help="Puerto (default: 8000)")
    parser.add_argument("--no-gmail", action="store_true", help="No arrancar el monitor de Gmail automáticamente")
    parser.add_argument("--intervalo", type=int, default=30, metavar="SEG", help="Intervalo del monitor Gmail (default: 30s)")
    args = parser.parse_args()

    if not args.no_gmail:
        gmail_monitor_state["intervalo"] = args.intervalo
        start_gmail_monitor(args.intervalo)
        print(f"  Monitor Gmail → cada {args.intervalo}s")

    import uvicorn
    print(f"  CrewAI Assistant → http://{args.host}:{args.port}\n")
    uvicorn.run(app, host=args.host, port=args.port, log_level="info")


if __name__ == "__main__":
    main()
