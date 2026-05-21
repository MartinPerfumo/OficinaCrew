"""
web_server.py — Interfaz web para CrewAI con monitor de Gmail integrado.

Uso:
  uv run python web_server.py              # Arranca en http://localhost:8000
  uv run python web_server.py --port 9000  # Puerto personalizado
  uv run python web_server.py --no-gmail   # Sin monitor de Gmail
"""

import argparse
import json
import logging
import os
import sys
import threading
import time
from datetime import datetime
from pathlib import Path

os.environ.setdefault("CREWAI_DISABLE_TELEMETRY", "true")
os.environ.setdefault("OTEL_SDK_DISABLED", "true")

from fastapi import FastAPI
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel

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
}
_gmail_lock = threading.Lock()
_gmail_stop_event = threading.Event()

MAX_MONITOR_LOGS = 50
MAX_EVENTS = 30


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
        from gmail_monitor import get_google_services, monitor_loop, get_new_emails, \
            get_unread_message_ids, procesar_email_con_crewai, build_readable_summary, \
            print_compact_status, build_action_notification, send_system_notification, \
            mark_as_read, add_label, create_calendar_event_from_text, extract_email_address, \
            send_email_reply, build_reply_body, cargar_state, guardar_state, _truncate

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

                            clasificacion = resultado.get("clasificacion", {})
                            categoria = str(clasificacion.get("categoria", "")).strip().lower()
                            _add_monitor_log(f"  → Categoría: {categoria}")
                            _add_event("email", f"Email procesado: {subject}",
                                       f"De: {sender} | Categoría: {categoria}")

                            if categoria in {"agenda", "ambos"}:
                                event_text = str(clasificacion.get("texto_agenda", "")).strip()
                                if not event_text:
                                    event_text = f"Reunión sobre: {subject}"
                                calendar_result = create_calendar_event_from_text(
                                    calendar_service, event_text, email
                                )
                                resultado["calendar_result"] = calendar_result
                                sender_email = extract_email_address(sender)

                                if calendar_result.get("created") and not calendar_result.get("conflict"):
                                    if sender_email:
                                        start_text = calendar_result.get("start")
                                        end_text = calendar_result.get("end")
                                        if start_text and end_text:
                                            confirmation_text = (
                                                "Hola,\n\n"
                                                f"La cita ha quedado confirmada para el "
                                                f"{datetime.fromisoformat(start_text).strftime('%d/%m/%Y a las %H:%M')} "
                                                f"hasta {datetime.fromisoformat(end_text).strftime('%H:%M')}.\n\n"
                                                "Queda reservada en mi agenda.\n\nUn saludo."
                                            )
                                            reply_body = build_reply_body(email, confirmation_text)
                                            try:
                                                send_email_reply(
                                                    service, sender_email,
                                                    email.get("subject", "Reunión"),
                                                    reply_body,
                                                    thread_id=email.get("thread_id"),
                                                    in_reply_to=email.get("message_id_header"),
                                                    references=email.get("references_header") or email.get("message_id_header"),
                                                )
                                                _add_monitor_log(f"  → Confirmación enviada a {sender_email}")
                                                _add_event(
                                                    "confirmacion",
                                                    f"Reunión confirmada: {subject}",
                                                    f"{datetime.fromisoformat(start_text).strftime('%d/%m/%Y %H:%M')} - "
                                                    f"{datetime.fromisoformat(end_text).strftime('%H:%M')} → {sender_email}",
                                                )
                                            except Exception as e:
                                                _add_monitor_log(f"  ⚠ Error enviando confirmación: {e}")

                                if calendar_result.get("conflict"):
                                    add_label(service, email["message_id"], label_name="CrewAI-Conflicto-Agenda")
                                    suggested_options = calendar_result.get("suggested_options", [])
                                    sender_email = extract_email_address(sender)
                                    if sender_email and suggested_options:
                                        options_lines = []
                                        for idx, option in enumerate(suggested_options[:3], start=1):
                                            option_start = datetime.fromisoformat(option["start"])
                                            option_end = datetime.fromisoformat(option["end"])
                                            dur = option.get("duration_minutes", 60)
                                            options_lines.append(
                                                f"{idx}. {option_start.strftime('%d/%m/%Y a las %H:%M')} hasta "
                                                f"{option_end.strftime('%H:%M')} ({dur} min)"
                                            )
                                        suggestion_text = (
                                            "Hola,\n\n"
                                            "He revisado mi agenda y no tengo hueco en la franja solicitada.\n\n"
                                            "Te propongo estas opciones alternativas:\n"
                                            + "\n".join(options_lines) + "\n\n"
                                            "Si te encaja alguna, responde indicando el número de opción.\n\nUn saludo."
                                        )
                                        reply_body = build_reply_body(email, suggestion_text)
                                        try:
                                            send_email_reply(
                                                service, sender_email,
                                                email.get("subject", "Reunión"),
                                                reply_body,
                                                thread_id=email.get("thread_id"),
                                                in_reply_to=email.get("message_id_header"),
                                                references=email.get("references_header") or email.get("message_id_header"),
                                            )
                                            _add_monitor_log(f"  → Alternativas enviadas a {sender_email}")
                                            _add_event(
                                                "conflicto",
                                                f"Conflicto de agenda: {subject}",
                                                f"Se enviaron {len(suggested_options[:3])} opciones alternativas a {sender_email}",
                                            )
                                        except Exception as e:
                                            _add_monitor_log(f"  ⚠ Error enviando alternativas: {e}")

                            mark_as_read(service, email["message_id"])
                            add_label(service, email["message_id"])
                        else:
                            _add_monitor_log(f"  ⚠ Error: {resultado.get('error', '?')}")

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

# ─── Endpoints API ─────────────────────────────────────────────────────────

@app.post("/api/peticion", response_model=PeticionResponse)
def procesar_peticion(req: PeticionRequest):
    """Recibe una petición de texto y la procesa con SupervisorFlow."""
    peticion = (req.peticion or "").strip()
    if not peticion:
        return PeticionResponse(success=False, error="La petición está vacía.")

    start_time = time.time()
    try:
        from src.ejemplo1.main import SupervisorFlow

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
                response = _handle_calendar_from_petition(
                    peticion, clasificacion, response
                )
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

    if not has_explicit_scheduling_intent(texto_agenda) and not has_explicit_scheduling_intent(peticion):
        return response

    reference_dt = datetime.now().astimezone()
    sources = [peticion, texto_agenda]
    start_dt, end_dt = _parse_event_datetimes_from_sources(sources, reference_dt)

    if not start_dt or not end_dt:
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
  }
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
  <div class="examples">
    <span class="example-chip" onclick="useExample(this)">Organiza una reunión mañana a las 10</span>
    <span class="example-chip" onclick="useExample(this)">Redacta un email al cliente sobre el retraso del proyecto</span>
    <span class="example-chip" onclick="useExample(this)">Busca documentos sobre vacaciones</span>
    <span class="example-chip" onclick="useExample(this)">Resume el documento politica_teletrabajo.md</span>
  </div>

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

  card.innerHTML = `
    <div class="meta">${meta}</div>
    <div class="result-text"><strong>Petición:</strong> ${escapeHtml(peticion)}\\n\\n${escapeHtml(body)}</div>
    ${calendarHtml}
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
</script>
</body>
</html>"""


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
