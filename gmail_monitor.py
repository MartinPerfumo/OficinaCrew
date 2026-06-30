"""
gmail_monitor.py - Monitor de Gmail que dispara SupervisorFlow automáticamente

Este script escucha la bandeja de entrada de Gmail y ejecuta el SupervisorFlow
cada vez que llega un email nuevo, generando automáticamente un resumen y
una respuesta sugerida.

Requisitos previos:
  1. Credenciales de Google Cloud (ver setup_gmail.py)
  2. Token de autenticación en ~/.gmail_token.json

Uso:
  uv run python gmail_monitor.py                    # Modo live (escucha continua)
  uv run python gmail_monitor.py --intervalo 60    # Verificar cada 60 segundos
  uv run python gmail_monitor.py --test             # Procesar últimos N emails sin marcarlos como leídos
"""

import argparse
import base64
import json
import logging
import os
import re
import sys
import time

# Forzar UTF-8 en stdout/stderr para evitar UnicodeEncodeError con emojis de CrewAI en Windows
if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    import io
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")
from textwrap import shorten
from datetime import datetime, timedelta
from pathlib import Path
from email.mime.text import MIMEText
from email.utils import parseaddr

# Evita que CrewAI registre telemetría y handlers de cierre que fallan en Python 3.13.
os.environ.setdefault("CREWAI_DISABLE_TELEMETRY", "true")
os.environ.setdefault("OTEL_SDK_DISABLED", "true")

try:
    from win10toast import ToastNotifier
except ImportError:
    ToastNotifier = None

try:
    from plyer import notification as system_notification
except ImportError:
    system_notification = None


def _normalize_email_analysis(data: dict) -> dict:
    """Normaliza la salida de análisis de urgencia/acciones con defaults seguros."""
    urgencia = str(data.get("urgencia", "no urgente")).strip().lower()
    if urgencia not in {"urgente", "no urgente", "trivial"}:
        urgencia = "no urgente"

    justificacion = str(data.get("justificacion_urgencia", "")).strip()
    if not justificacion:
        justificacion = "Sin indicadores claros de urgencia extrema."

    raw_actions = data.get("acciones_pendientes", [])
    acciones = []
    if isinstance(raw_actions, list):
        for item in raw_actions:
            if not isinstance(item, dict):
                continue
            accion = str(item.get("accion", "")).strip()
            if not accion:
                continue
            prioridad = str(item.get("prioridad", "media")).strip().lower()
            if prioridad not in {"alta", "media", "baja"}:
                prioridad = "media"
            acciones.append(
                {
                    "accion": accion,
                    "responsable": str(item.get("responsable", "")).strip(),
                    "fecha_limite": str(item.get("fecha_limite", "")).strip(),
                    "prioridad": prioridad,
                }
            )

    return {
        "urgencia": urgencia,
        "justificacion_urgencia": justificacion,
        "acciones_pendientes": acciones,
    }


def analyze_email_urgency_and_actions(email: dict) -> dict:
    """Analiza urgencia y acciones pendientes (RF1/RF3) con fallback robusto."""
    try:
        sys.path.insert(0, str(Path(__file__).parent / "src"))
        from src.oficinacrew.main import _call_llm_with_fallback

        from datetime import date
        today_str = date.today().strftime("%d/%m/%Y")
        prompt = f"""Analiza este email y responde SOLO con JSON válido.

Hoy es {today_str}.

Email:
De: {email.get('sender', '')}
Asunto: {email.get('subject', '')}
Cuerpo:
{email.get('body', '')}

Devuelve este formato exacto:
{{
  "urgencia": "urgente|no urgente|trivial",
  "justificacion_urgencia": "1-2 frases breves justificando la urgencia",
  "acciones_pendientes": [
    {{
      "accion": "tarea concreta",
      "responsable": "persona o equipo si aparece, o vacío",
      "fecha_limite": "DD/MM/YYYY HH:MM si hay fecha, o vacío",
      "prioridad": "alta|media|baja"
    }}
  ]
}}

Reglas:
- No inventes datos que no estén en el email.
- Si no hay acciones, devuelve una lista vacía.
- La urgencia debe ser solo una de: urgente, no urgente, trivial.
- Usa "urgente" SOLO si el contenido expresa inmediatez extrema con palabras como "urgente", "ASAP", "hoy mismo", "bloqueante", "crítico", "emergencia". Una cita, reunión o solicitud rutinaria —aunque sea para mañana— es "no urgente".
- fecha_limite SIEMPRE debe ser una fecha absoluta en formato DD/MM/YYYY HH:MM, nunca texto relativo como "la semana que viene".
- Si el email menciona un día relativo (mañana, el viernes, la semana que viene...), calcúlalo desde hoy ({today_str}) y escribe la fecha absoluta.
- Si no se menciona hora, usa 17:30 como hora por defecto.
- Si no hay ninguna fecha límite en el email, deja el campo vacío.
"""
        text = _call_llm_with_fallback(prompt)
        start_idx = text.find("{")
        end_idx = text.rfind("}") + 1
        if start_idx != -1 and end_idx > start_idx:
            parsed = json.loads(text[start_idx:end_idx])
            return _normalize_email_analysis(parsed)
    except Exception:
        pass

    body_lower = str(email.get("body", "")).lower()
    urgent_hits = sum(
        token in body_lower
        for token in ["urgente", "asap", "inmediato", "hoy", "cuanto antes", "bloqueante", "crítico"]
    )
    if urgent_hits >= 2:
        urgencia = "urgente"
        justificacion = "El mensaje contiene varios indicadores de inmediatez."
    elif urgent_hits == 0 and len(body_lower.strip()) < 120:
        urgencia = "trivial"
        justificacion = "El contenido es breve y no incluye solicitudes críticas."
    else:
        urgencia = "no urgente"
        justificacion = "Hay solicitud o información relevante, pero sin carácter crítico explícito."

    actions = []
    for line in str(email.get("body", "")).splitlines():
        raw = line.strip()
        low = raw.lower()
        if not raw:
            continue
        if any(k in low for k in ["por favor", "necesito", "tienes que", "debes", "solicito", "hay que"]):
            actions.append(
                {
                    "accion": raw,
                    "responsable": "",
                    "fecha_limite": "",
                    "prioridad": "media",
                }
            )

    return _normalize_email_analysis(
        {
            "urgencia": urgencia,
            "justificacion_urgencia": justificacion,
            "acciones_pendientes": actions,
        }
    )

try:
    from google.auth.transport.requests import Request
    from google.oauth2.service_account import Credentials
    from google.oauth2.credentials import Credentials as UserCredentials
    from google_auth_oauthlib.flow import InstalledAppFlow
    import googleapiclient.discovery
except ImportError as e:
    print("Error: Dependencias de Google no instaladas.")
    print(f"Detalle: {e}")
    print("Ejecuta: uv add 'google-auth-oauthlib>=1.1.0' 'google-auth-httplib2>=0.2.0' 'google-api-python-client>=2.0.0'")
    sys.exit(1)

# ─── Logging ───────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="[%(asctime)s] %(levelname)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S"
)
logger = logging.getLogger(__name__)

_toast_notifier = ToastNotifier() if ToastNotifier is not None else None


def _truncate(text: str, limit: int = 80) -> str:
    return shorten((text or "").replace("\n", " ").strip(), width=limit, placeholder="...")


def build_readable_summary(email: dict, resultado: dict) -> str:
    """Construye un resumen legible del procesamiento del email."""
    analisis = resultado.get("analisis_email", {}) if isinstance(resultado, dict) else {}
    clasificacion = resultado.get("clasificacion", {}) if isinstance(resultado, dict) else {}
    urgency = analisis.get("urgencia", "no urgente")
    justificacion = _truncate(str(analisis.get("justificacion_urgencia", "")), 90)
    categoria = str(clasificacion.get("categoria", "")).strip() or "-"
    resumen = _truncate(str(clasificacion.get("resumen", email.get("subject", ""))), 110)

    acciones = analisis.get("acciones_pendientes", []) if isinstance(analisis, dict) else []
    actions_count = len(acciones) if isinstance(acciones, list) else 0
    lines = [
        f"Asunto: {email.get('subject', 'Sin asunto')}",
        f"Categoria: {categoria} | Urgencia: {urgency}",
        f"Resumen: {resumen}",
        f"Justificacion: {justificacion}",
        f"Acciones: {actions_count}",
    ]
    return "\n".join(lines)


def build_action_notification(email: dict, resultado: dict) -> tuple[str, str]:
    """Construye título y mensaje breves para la notificación del sistema."""
    analisis = resultado.get("analisis_email", {}) if isinstance(resultado, dict) else {}
    clasificacion = resultado.get("clasificacion", {}) if isinstance(resultado, dict) else {}
    categoria = str(clasificacion.get("categoria", "")).strip() or "documentos"
    urgencia = str(analisis.get("urgencia", "no urgente")).strip()
    subject = _truncate(email.get("subject", "Sin asunto"), 48)

    action_bits = []
    calendar_result = resultado.get("calendar_result", {}) if isinstance(resultado, dict) else {}
    if isinstance(calendar_result, dict) and calendar_result.get("created"):
        if calendar_result.get("conflict"):
            action_bits.append("conflicto detectado")
        else:
            action_bits.append("cita creada")

    actions = analisis.get("acciones_pendientes", []) if isinstance(analisis, dict) else []
    if actions:
        action_bits.append(f"{len(actions)} acción(es)")

    if not action_bits:
        action_bits.append("procesado")

    title = f"{categoria.capitalize()} | {subject}"
    message = f"Urgencia: {urgencia}. {'; '.join(action_bits)}."
    return title, message


def send_system_notification(title: str, message: str):
    """Muestra una notificación nativa del sistema si está disponible."""
    try:
        if _toast_notifier is not None:
            _toast_notifier.show_toast(
                title=title,
                msg=message,
                icon_path=None,
                duration=8,
                threaded=True,
            )
            return
        if system_notification is not None:
            system_notification.notify(
                title=title,
                message=message,
                app_name="OficinaCrew Gmail Monitor",
                timeout=8,
            )
    except Exception as e:
        logger.debug(f"No se pudo mostrar notificación del sistema: {e}")


def print_compact_status(email: dict, resultado: dict):
    """Imprime una salida compacta y más legible en terminal."""
    analisis = resultado.get("analisis_email", {}) if isinstance(resultado, dict) else {}
    clasificacion = resultado.get("clasificacion", {}) if isinstance(resultado, dict) else {}
    calendar_result = resultado.get("calendar_result", {}) if isinstance(resultado, dict) else {}

    lines = [
        f"[{email.get('subject', 'Sin asunto')}]",
        f"  Categoria : {clasificacion.get('categoria', '-')}",
        f"  Urgencia  : {analisis.get('urgencia', 'no urgente')}",
        f"  Resumen   : {_truncate(str(clasificacion.get('resumen', email.get('subject', ''))), 100)}",
    ]

    if isinstance(calendar_result, dict):
        if calendar_result.get("created"):
            if calendar_result.get("conflict"):
                lines.append("  Calendario: conflicto detectado; se propuso alternativa")
            else:
                lines.append("  Calendario: cita creada y confirmada")
        elif calendar_result.get("reason") == "no_scheduling_intent":
            lines.append("  Calendario: sin intención explícita de agendar")

    acciones = analisis.get("acciones_pendientes", []) if isinstance(analisis, dict) else []
    if acciones:
        lines.append(f"  Acciones  : {len(acciones)} detectadas")

    print("\n" + "═" * 72)
    print("RESUMEN")
    print("═" * 72)
    print("\n".join(lines))
    print("═" * 72 + "\n")

# ─── Configuración ────────────────────────────────────────────────────────
TOKEN_FILE = Path.home() / ".gmail_token.json"
CREDENTIALS_FILE = Path.home() / ".gmail_credentials.json"
STATE_FILE = Path(__file__).parent / ".gmail_monitor_state.json"

CALENDAR_SCOPE = "https://www.googleapis.com/auth/calendar.events"
SCOPES = [
    "https://www.googleapis.com/auth/gmail.readonly",
    "https://www.googleapis.com/auth/gmail.modify",
    CALENDAR_SCOPE,
    "https://www.googleapis.com/auth/gmail.send",
]

# ─── Autenticación ────────────────────────────────────────────────────────

def get_google_services():
    """Obtiene los servicios de Gmail y Calendar con autenticación OAuth2."""
    creds = None

    # Cargar credenciales guardadas
    if TOKEN_FILE.exists():
        creds = UserCredentials.from_authorized_user_file(TOKEN_FILE, SCOPES)

    # Reautenticar si el token existente no incluye todos los scopes requeridos
    if creds and not creds.has_scopes(SCOPES):
        creds = None

    # Si no hay credenciales válidas, obtener nuevas
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            if not CREDENTIALS_FILE.exists():
                logger.error(f"Archivo de credenciales no encontrado: {CREDENTIALS_FILE}")
                logger.error("Ejecuta primero: python setup_gmail.py")
                sys.exit(1)

            flow = InstalledAppFlow.from_client_secrets_file(
                CREDENTIALS_FILE, SCOPES
            )
            creds = flow.run_local_server(port=0)

        # Guardar credenciales para futuros usos
        with open(TOKEN_FILE, "w") as token:
            token.write(creds.to_json())
        logger.info(f"Credenciales guardadas en {TOKEN_FILE}")

    gmail_service = googleapiclient.discovery.build("gmail", "v1", credentials=creds)
    calendar_service = googleapiclient.discovery.build("calendar", "v3", credentials=creds)
    return gmail_service, calendar_service

# ─── Funciones de Gmail ────────────────────────────────────────────────────

def get_email_body(service, message_id: str) -> dict:
    """Extrae el cuerpo de un email."""
    try:
        message = service.users().messages().get(userId="me", id=message_id, format="full").execute()
        thread_id = message.get("threadId", "")
        headers = message["payload"]["headers"]
        subject = next((h["value"] for h in headers if h["name"] == "Subject"), "Sin asunto")
        sender = next((h["value"] for h in headers if h["name"] == "From"), "Desconocido")
        message_id_header = next((h["value"] for h in headers if h["name"].lower() == "message-id"), "")
        references_header = next((h["value"] for h in headers if h["name"].lower() == "references"), "")

        # Intentar extraer el cuerpo (plain text primero, HTML como fallback)
        body = ""
        html_body = ""
        if "parts" in message["payload"]:
            for part in message["payload"]["parts"]:
                if part["mimeType"] == "text/plain":
                    data = part["body"].get("data", "")
                    if data:
                        body = base64.urlsafe_b64decode(data).decode("utf-8")
                    break
                elif part["mimeType"] == "text/html" and not html_body:
                    data = part["body"].get("data", "")
                    if data:
                        raw_html = base64.urlsafe_b64decode(data).decode("utf-8")
                        # Eliminar etiquetas HTML básicas para quedarse solo con texto
                        html_body = re.sub(r"<[^>]+>", " ", raw_html)
                        html_body = re.sub(r"\s+", " ", html_body).strip()
        else:
            data = message["payload"]["body"].get("data", "")
            if data:
                body = base64.urlsafe_b64decode(data).decode("utf-8")
        # Si no hay texto plano, usar el HTML limpio como fallback
        if not body.strip() and html_body:
            body = html_body

        return {
            "message_id": message_id,
            "thread_id": thread_id,
            "message_id_header": message_id_header,
            "references_header": references_header,
            "subject": subject,
            "sender": sender,
            "body": body[:2000],  # Limitar a 2000 caracteres
        }
    except Exception as e:
        logger.error(f"Error extrayendo email {message_id}: {e}")
        return None

def get_new_emails(service, max_results: int = 5, include_read: bool = False) -> list[dict]:
    """Obtiene los últimos emails de la bandeja de entrada.
    
    Args:
        service: Gmail API service
        max_results: Número máximo de emails a obtener
        include_read: Si True, incluye también emails ya leídos (útil para testing)
    """
    try:
        query = "in:inbox" if include_read else "is:unread"
        results = (
            service.users()
            .messages()
            .list(userId="me", q=query, maxResults=max_results)
            .execute()
        )
        messages = results.get("messages", [])

        emails = []
        for msg in messages:
            email = get_email_body(service, msg["id"])
            if email:
                emails.append(email)

        return emails
    except Exception as e:
        logger.error(f"Error obteniendo emails: {e}")
        return []

def get_unread_message_ids(service, max_results: int = 500) -> set[str]:
    """Obtiene IDs de emails no leídos para crear una línea base inicial."""
    ids = set()
    page_token = None

    try:
        while len(ids) < max_results:
            request = service.users().messages().list(
                userId="me",
                q="is:unread",
                maxResults=min(500, max_results - len(ids)),
                pageToken=page_token,
            )
            results = request.execute()
            messages = results.get("messages", [])

            if not messages:
                break

            for msg in messages:
                ids.add(msg["id"])

            page_token = results.get("nextPageToken")
            if not page_token:
                break
    except Exception as e:
        logger.error(f"Error obteniendo IDs no leídos para baseline: {e}")

    return ids

def mark_as_read(service, message_id: str):
    """Marca un email como leído."""
    try:
        service.users().messages().modify(
            userId="me",
            id=message_id,
            body={"removeLabelIds": ["UNREAD"]},
        ).execute()
    except Exception as e:
        logger.error(f"Error marcando email {message_id} como leído: {e}")

def add_label(service, message_id: str, label_name: str = "CrewAI-Procesado"):
    """Añade una etiqueta a un email."""
    try:
        # Obtener o crear la etiqueta
        results = service.users().labels().list(userId="me").execute()
        labels = results.get("labels", [])
        label_id = next(
            (l["id"] for l in labels if l["name"] == label_name),
            None
        )

        if not label_id:
            label = service.users().labels().create(
                userId="me",
                body={"name": label_name, "labelListVisibility": "labelShow"}
            ).execute()
            label_id = label["id"]

        service.users().messages().modify(
            userId="me",
            id=message_id,
            body={"addLabelIds": [label_id]},
        ).execute()
    except Exception as e:
        logger.error(f"Error añadiendo etiqueta a {message_id}: {e}")

def extract_email_address(sender_header: str) -> str:
    """Extrae la dirección de email desde el header From."""
    _, email_address = parseaddr(sender_header or "")
    return email_address.strip()

def send_email_reply(
    service,
    to_email: str,
    subject: str,
    body: str,
    thread_id: str | None = None,
    in_reply_to: str | None = None,
    references: str | None = None,
):
    """Envía un correo simple por Gmail API."""
    message = MIMEText(body, "plain", "utf-8")
    message["To"] = to_email
    message["Subject"] = subject.strip()
    if in_reply_to:
        message["In-Reply-To"] = in_reply_to
    if references:
        message["References"] = references
    elif in_reply_to:
        message["References"] = in_reply_to

    raw = base64.urlsafe_b64encode(message.as_bytes()).decode("utf-8")
    body_payload = {"message": {"raw": raw}}
    if thread_id:
        body_payload["message"]["threadId"] = thread_id
    draft = service.users().drafts().create(userId="me", body=body_payload).execute()
    return service.users().drafts().send(
        userId="me",
        body={
            "id": draft["id"],
            "message": {
                "raw": raw,
                **({"threadId": thread_id} if thread_id else {}),
            },
        },
    ).execute()


def create_email_reply_draft(
    service,
    to_email: str,
    subject: str,
    body: str,
    thread_id: str | None = None,
    in_reply_to: str | None = None,
    references: str | None = None,
):
    """Crea un borrador de respuesta en Gmail (sin enviarlo)."""
    message = MIMEText(body, "plain", "utf-8")
    message["To"] = to_email
    message["Subject"] = subject.strip()
    if in_reply_to:
        message["In-Reply-To"] = in_reply_to
    if references:
        message["References"] = references
    elif in_reply_to:
        message["References"] = in_reply_to

    raw = base64.urlsafe_b64encode(message.as_bytes()).decode("utf-8")
    body_payload = {"message": {"raw": raw}}
    if thread_id:
        body_payload["message"]["threadId"] = thread_id
    return service.users().drafts().create(userId="me", body=body_payload).execute()

def build_reply_body(email: dict, suggestion_text: str) -> str:
    """Construye la respuesta incluyendo el correo original citado al final."""
    original_subject = email.get("subject", "Sin asunto")
    original_sender = email.get("sender", "Desconocido")
    original_body = (email.get("body", "") or "").strip()

    quoted_original = "\n".join(
        [
            "--- Correo original ---",
            f"De: {original_sender}",
            f"Asunto: {original_subject}",
            "",
            original_body,
        ]
    ).strip()

    return f"{suggestion_text.strip()}\n\n{quoted_original}\n"


def extract_latest_reply_content(text: str) -> str:
    """Recorta el contenido citado de una respuesta y deja solo el mensaje nuevo."""
    if not text:
        return ""

    normalized = text.replace("\r\n", "\n")

    delimiter_patterns = [
        r"\n---\s*correo original\s*---",
        r"\nOn .+?wrote:",
        r"\nEl .+?escribi[oó]:",
        r"\nDe:\s.+\nAsunto:\s.+",
        r"\nFrom:\s.+\nSubject:\s.+",
    ]

    cut_positions = []
    for pattern in delimiter_patterns:
        match = re.search(pattern, normalized, flags=re.IGNORECASE | re.DOTALL)
        if match:
            cut_positions.append(match.start())

    if cut_positions:
        normalized = normalized[: min(cut_positions)]

    cleaned_lines = []
    for line in normalized.split("\n"):
        stripped = line.strip()
        if stripped.startswith(">"):
            continue
        if re.match(r"^(de|from|asunto|subject|to|para):\s", stripped, flags=re.IGNORECASE):
            break
        if re.match(r"^(el\s+.+escribi[oó]:|on\s+.+wrote:)$", stripped, flags=re.IGNORECASE):
            break
        cleaned_lines.append(line)

    return "\n".join(cleaned_lines).strip()


def parse_selected_alternative_slot(
    latest_reply_text: str,
    raw_email_body: str,
    reference_dt: datetime,
) -> tuple[datetime, datetime] | None:
    """Si el usuario elige una opción por número, extrae su franja desde el correo citado."""
    selection_text = (latest_reply_text or "").lower()
    selection_match = re.search(
        r"\b(opcion|opción)\b\s*(?:n[uú]mero\s*)?(\d{1,2})\b",
        selection_text,
    )
    if not selection_match:
        return None

    selected_number = int(selection_match.group(2))
    if selected_number <= 0:
        return None

    option_lines = re.findall(
        r"(?mi)^\s*(\d+)\.\s*(\d{1,2}/\d{1,2}/\d{2,4})\s+a\s+las\s+([01]?\d|2[0-3]):([0-5]\d)\s+hasta\s+([01]?\d|2[0-3]):([0-5]\d)",
        raw_email_body or "",
    )

    for number_str, date_str, start_h, start_m, end_h, end_m in option_lines:
        if int(number_str) != selected_number:
            continue

        day, month, year = [int(part) for part in date_str.split("/")]
        if year < 100:
            year += 2000

        tzinfo = reference_dt.tzinfo
        start_dt = datetime(year, month, day, int(start_h), int(start_m), tzinfo=tzinfo)
        end_dt = datetime(year, month, day, int(end_h), int(end_m), tzinfo=tzinfo)
        if end_dt <= start_dt:
            end_dt = start_dt + timedelta(hours=1)
        return start_dt, end_dt

    return None


def _extract_explicit_time(text: str) -> tuple[int, int] | None:
    """Extrae una hora explícita si aparece; si no, devuelve None."""
    normalized = text.lower()

    # Formato AM/PM americano: "3pm", "3 pm", "3:30pm", "13pm" → convertir a 24h primero
    ampm_match = re.search(r"\b(1[0-2]|0?[1-9])(?::([0-5]\d))?\s*(am|pm)\b", normalized)
    if ampm_match:
        hour = int(ampm_match.group(1))
        minute = int(ampm_match.group(2)) if ampm_match.group(2) else 0
        suffix = ampm_match.group(3)
        if suffix == "pm" and hour != 12:
            hour += 12
        elif suffix == "am" and hour == 12:
            hour = 0
        return hour, minute

    patterns = [
        r"\b(?:a\s+las\s+)?([01]?\d|2[0-3]):([0-5]\d)\b",
        r"\b(?:a\s+las\s+)?([01]?\d|2[0-3]):([0-5]\d)\s*(?:h|hs|hrs?|horas?)\b",
        r"\b([01]?\d|2[0-3])\s+y\s+media\b",
        r"\b([01]?\d|2[0-3])\s+y\s+cuarto\b",
        r"\ba\s+las\s+([01]?\d|2[0-3])(?:\s*(?:h|hs|hrs?|horas?))?\b",
        r"\b([01]?\d|2[0-3])\s*(?:h|hs|hrs?|horas?)\b",
        r"\b([01]?\d|2[0-3])(?:h|hs|hrs|horas)\b",
        r"\b([01]?\d|2[0-3])(?:h|hs|hrs?)0{1,2}\b",
        r"\b([01]?\d|2[0-3])\.(?:0)?([0-5]\d)\b",
        r"\b([01]?\d|2[0-3])\s+horas?\b",
    ]

    matches = []
    for idx, pattern in enumerate(patterns):
        match = re.search(pattern, normalized)
        if match:
            matches.append((match.start(), idx, match))

    if not matches:
        return None

    _, pattern_idx, match = min(matches, key=lambda item: (item[0], item[1]))
    if pattern_idx == 2:
        return int(match.group(1)), 30
    if pattern_idx == 3:
        return int(match.group(1)), 15
    if pattern_idx in {0, 1, 8}:
        return int(match.group(1)), int(match.group(2))
    return int(match.group(1)), 0

WEEKDAY_MAP = {
    "lunes": 0,
    "martes": 1,
    "miercoles": 2,
    "miércoles": 2,
    "jueves": 3,
    "viernes": 4,
    "sabado": 5,
    "sábado": 5,
    "domingo": 6,
}

MONTH_MAP = {
    "enero": 1,
    "febrero": 2,
    "marzo": 3,
    "abril": 4,
    "mayo": 5,
    "junio": 6,
    "julio": 7,
    "agosto": 8,
    "septiembre": 9,
    "setiembre": 9,
    "octubre": 10,
    "noviembre": 11,
    "diciembre": 12,
}

def _extract_time(text: str) -> tuple[int, int]:
    """Extrae hora:minuto en formato común; usa 09:00 por defecto."""
    explicit_time = _extract_explicit_time(text)
    if explicit_time is not None:
        return explicit_time
    return 9, 0

def _extract_date(text: str, base_dt: datetime):
    """Extrae fecha del texto (dd/mm, dd de mes, lunes, mañana)."""
    normalized = text.lower()

    # 1) Fechas numéricas: 25/05/2026 o 25-05
    numeric_date = re.search(r"\b(\d{1,2})[/-](\d{1,2})(?:[/-](\d{2,4}))?\b", normalized)
    if numeric_date:
        day = int(numeric_date.group(1))
        month = int(numeric_date.group(2))
        year_raw = numeric_date.group(3)
        year = base_dt.year
        if year_raw:
            year = int(year_raw)
            if year < 100:
                year += 2000
        try:
            candidate = datetime(year, month, day)
            if not year_raw and candidate.date() < base_dt.date():
                candidate = datetime(year + 1, month, day)
            return candidate.date()
        except ValueError:
            pass

    # 2) Fechas en texto: 25 de mayo (de 2026)
    text_date = re.search(
        r"\b(\d{1,2})\s+de\s+([a-záéíóú]+)(?:\s+de\s+(\d{4}))?\b",
        normalized,
    )
    if text_date:
        day = int(text_date.group(1))
        month_name = text_date.group(2)
        month = MONTH_MAP.get(month_name)
        if month:
            year = int(text_date.group(3)) if text_date.group(3) else base_dt.year
            try:
                candidate = datetime(year, month, day)
                if not text_date.group(3) and candidate.date() < base_dt.date():
                    candidate = datetime(year + 1, month, day)
                return candidate.date()
            except ValueError:
                pass

    # 2.b) Día del mes sin mes explícito: "el día 21" o "el 21"
    day_only = re.search(r"\b(?:el\s+d[ií]a\s+|d[ií]a\s+|el\s+)(\d{1,2})\b", normalized)
    if day_only:
        day = int(day_only.group(1))
        month = base_dt.month
        year = base_dt.year
        try:
            candidate = datetime(year, month, day)
            if candidate.date() < base_dt.date():
                month += 1
                if month > 12:
                    month = 1
                    year += 1
                candidate = datetime(year, month, day)
            return candidate.date()
        except ValueError:
            pass

    # 3) Día de la semana con modificadores explícitos: "viernes que viene", "próximo viernes"
    weekday_with_modifier = re.search(
        r"\b(?:el\s+)?(?:(proximo|próximo|siguiente)\s+)?(lunes|martes|miercoles|miércoles|jueves|viernes|sabado|sábado|domingo)(?:\s+(que\s+viene|pr[oó]ximo|siguiente))?\b",
        normalized,
    )
    if weekday_with_modifier:
        weekday_name = weekday_with_modifier.group(2)
        target_weekday = WEEKDAY_MAP.get(weekday_name)
        if target_weekday is not None:
            days_ahead = (target_weekday - base_dt.weekday()) % 7
            if days_ahead == 0:
                days_ahead = 7

            if re.search(r"\b(la\s+semana\s+que\s+viene|la\s+pr[oó]xima\s+semana|la\s+semana\s+siguiente)\b", normalized):
                days_ahead += 7

            return (base_dt + timedelta(days=days_ahead)).date()

    # 4) Referencias relativas simples
    if "pasado manana" in normalized or "pasado mañana" in normalized:
        return (base_dt + timedelta(days=2)).date()
    if re.search(r"(?<!de\sla\s)(?<!por\sla\s)\b(mañana|manana)\b", normalized):
        return (base_dt + timedelta(days=1)).date()
    if "hoy" in normalized:
        return base_dt.date()

    # 5) Día de la semana en español
    for name, target_weekday in WEEKDAY_MAP.items():
        if re.search(rf"\b{name}\b", normalized):
            days_ahead = (target_weekday - base_dt.weekday()) % 7
            if days_ahead == 0:
                days_ahead = 7
            return (base_dt + timedelta(days=days_ahead)).date()

    return None

def _parse_event_datetimes(text: str, reference_dt: datetime):
    """Devuelve inicio y fin cuando hay fecha explícita; si no, devuelve None."""
    date_value = _extract_date(text, reference_dt)
    if not date_value:
        return None, None

    hour, minute = _extract_time(text)
    tzinfo = reference_dt.tzinfo
    start_dt = datetime(
        date_value.year,
        date_value.month,
        date_value.day,
        hour,
        minute,
        tzinfo=tzinfo,
    )
    end_dt = start_dt + timedelta(hours=1)
    return start_dt, end_dt


def _parse_event_datetimes_from_sources(sources: list[str], reference_dt: datetime):
    """Extrae fecha/hora priorizando bloques concretos en orden y evitando contaminación cruzada."""
    fallback_start = None
    fallback_end = None

    for source in sources:
        source = (source or "").strip()
        if not source:
            continue

        date_value = _extract_date(source, reference_dt)
        if not date_value:
            continue

        explicit_time = _extract_explicit_time(source)
        if explicit_time is not None:
            hour, minute = explicit_time
            start_dt = datetime(
                date_value.year,
                date_value.month,
                date_value.day,
                hour,
                minute,
                tzinfo=reference_dt.tzinfo,
            )
            return start_dt, start_dt + timedelta(hours=1)

        if fallback_start is None:
            fallback_start = datetime(
                date_value.year,
                date_value.month,
                date_value.day,
                9,
                0,
                tzinfo=reference_dt.tzinfo,
            )
            fallback_end = fallback_start + timedelta(hours=1)

    return fallback_start, fallback_end

def has_agenda_summary_intent(text: str) -> bool:
    """Detecta si el texto es una consulta de resumen/consulta de agenda (no de creación de evento)."""
    normalized = (text or "").lower()
    return bool(re.search(
        # Preguntas directas: "qué X tengo", "qué tengo", "qué hay"
        r"qu[eé]\s+(\w+\s+)?tengo|qu[eé]\s+hay"
        # Consultar/ver agenda
        r"|ver\s+(mi\s+)?agenda|mi\s+agenda|mis\s+eventos|mis\s+citas"
        # Resumen de agenda/semana/mes/día
        r"|resumen\s+de\s+(mi\s+)?(agenda|semana|mes|d[ií]a)"
        r"|hazme\s+(un\s+)?resumen|hacer\s+(un\s+)?resumen\s+de"
        # Consultar agenda
        r"|consultar?\s+(mi\s+)?agenda"
        # Eventos/citas/reuniones que tengo
        r"|(citas?|reuniones?|eventos?)\s+(que\s+)?tengo"
        r"|tengo\s+(algo|alguna?\s+(cita|reuni[oó]n|cosa)|alg[uú]n\s+evento)"
        r"|hay\s+algo|hay\s+alguna?\s+(cita|reuni[oó]n|evento)"
        # Formas con día específico: "tengo algo el viernes", "tengo alguna cita mañana"
        r"|tengo\s+algo\s+(el|la|este|esta|mañana|hoy|pa[sí]ado)"
        # Cuantos eventos/citas
        r"|cu[aá]ntos?\s+(eventos?|citas?|reuniones?)"
        # Agenda del/de la/para
        r"|agenda\s+(del?|de\s+la\s+|para\s+|esta\s+|de\s+esta\s+|la\s+pr[oó]xima\s+)"
        # Disponibilidad
        r"|disponibilidad",
        normalized,
    ))


def parse_date_range_for_summary(text: str, reference_dt: datetime) -> tuple[datetime, datetime]:
    """
    Analiza el texto y devuelve (start_dt, end_dt) para consulta de agenda.
    Por defecto devuelve los próximos 7 días desde hoy.
    """
    normalized = (text or "").lower()
    today = reference_dt.date()
    tz = reference_dt.tzinfo

    def _start_of_day(d):
        return datetime(d.year, d.month, d.day, 0, 0, 0, tzinfo=tz)

    def _end_of_day(d):
        return datetime(d.year, d.month, d.day, 23, 59, 59, tzinfo=tz)

    # "pasado mañana"
    if re.search(r"\bpasado\s*(mañana|manana)\b", normalized):
        day_after = today + timedelta(days=2)
        return _start_of_day(day_after), _end_of_day(day_after)

    # "mañana" (pero no "pasado mañana")
    if re.search(r"\b(mañana|manana)\b", normalized):
        tomorrow = today + timedelta(days=1)
        return _start_of_day(tomorrow), _end_of_day(tomorrow)

    # "hoy"
    if re.search(r"\bhoy\b", normalized):
        return _start_of_day(today), _end_of_day(today)

    # "próxima semana" / "la semana que viene"
    if re.search(r"\b(pr[oó]xima\s+semana|semana\s+que\s+viene|siguiente\s+semana)\b", normalized):
        monday = today - timedelta(days=today.weekday()) + timedelta(weeks=1)
        sunday = monday + timedelta(days=6)
        return _start_of_day(monday), _end_of_day(sunday)

    # "esta semana" / "la semana"
    if re.search(r"\b(esta\s+semana|semana\s+actual|la\s+semana)\b", normalized):
        monday = today - timedelta(days=today.weekday())
        sunday = monday + timedelta(days=6)
        return _start_of_day(monday), _end_of_day(sunday)

    # "este mes"
    if re.search(r"\b(este\s+mes|mes\s+actual)\b", normalized):
        import calendar as cal_mod
        start = today.replace(day=1)
        last_day = cal_mod.monthrange(today.year, today.month)[1]
        end = today.replace(day=last_day)
        return _start_of_day(start), _end_of_day(end)

    # Rango explícito: "del 5 al 10 de junio"
    range_match = re.search(
        r"\bdel?\s+(\d{1,2})\s+al?\s+(\d{1,2})\s+de\s+([a-záéíóú]+)\b",
        normalized,
    )
    if range_match:
        day1 = int(range_match.group(1))
        day2 = int(range_match.group(2))
        month = MONTH_MAP.get(range_match.group(3))
        if month:
            year = reference_dt.year
            try:
                from datetime import date as _date
                return _start_of_day(_date(year, month, day1)), _end_of_day(_date(year, month, day2))
            except ValueError:
                pass

    # Fecha única: "25 de junio"
    single_match = re.search(
        r"\b(\d{1,2})\s+de\s+([a-záéíóú]+)(?:\s+de\s+(\d{4}))?\b",
        normalized,
    )
    if single_match:
        day = int(single_match.group(1))
        month = MONTH_MAP.get(single_match.group(2))
        if month:
            year = int(single_match.group(3)) if single_match.group(3) else reference_dt.year
            try:
                from datetime import date as _date
                d = _date(year, month, day)
                return _start_of_day(d), _end_of_day(d)
            except ValueError:
                pass

    # Día de la semana: "el lunes", "el viernes"
    for name, weekday_num in WEEKDAY_MAP.items():
        if re.search(rf"\b{name}\b", normalized):
            days_ahead = (weekday_num - today.weekday()) % 7
            if days_ahead == 0:
                days_ahead = 7
            target_day = today + timedelta(days=days_ahead)
            return _start_of_day(target_day), _end_of_day(target_day)

    # Por defecto: próximos 7 días
    return _start_of_day(today), _end_of_day(today + timedelta(days=7))


def build_agenda_summary_text(events: list[dict], start_dt: datetime, end_dt: datetime) -> str:
    """Formatea un resumen legible de eventos del calendario, marcando conflictos."""
    DAYS_ES = ["Lunes", "Martes", "Miércoles", "Jueves", "Viernes", "Sábado", "Domingo"]
    MONTHS_ES = [
        "enero", "febrero", "marzo", "abril", "mayo", "junio",
        "julio", "agosto", "septiembre", "octubre", "noviembre", "diciembre"
    ]
    tz = start_dt.tzinfo

    same_day = start_dt.date() == end_dt.date()
    if same_day:
        period = f"del {DAYS_ES[start_dt.weekday()]} {start_dt.day} de {MONTHS_ES[start_dt.month - 1]}"
    else:
        period = (
            f"del {start_dt.day} de {MONTHS_ES[start_dt.month - 1]}"
            f" al {end_dt.day} de {MONTHS_ES[end_dt.month - 1]}"
        )

    if not events:
        return f"No hay eventos programados {period}."

    # Agrupar por día
    from collections import defaultdict
    by_day: dict = defaultdict(list)
    for ev in events:
        start_val = ev.get("start", {})
        dt_str = start_val.get("dateTime") or start_val.get("date", "")
        if "T" in dt_str:
            ev_start = datetime.fromisoformat(dt_str.replace("Z", "+00:00")).astimezone(tz)
        else:
            ev_start = datetime.fromisoformat(dt_str).replace(hour=0, minute=0, tzinfo=tz)
        by_day[ev_start.date()].append((ev_start, ev))

    # Detectar conflictos (solapamientos dentro del mismo día)
    conflict_ids: set[str] = set()
    for day_events in by_day.values():
        sorted_evs = sorted(day_events, key=lambda x: x[0])
        for i in range(len(sorted_evs)):
            ev_i_start, ev_i = sorted_evs[i]
            ev_i_end_str = (ev_i.get("end", {}).get("dateTime") or ev_i.get("end", {}).get("date", ""))
            if "T" in ev_i_end_str:
                ev_i_end = datetime.fromisoformat(ev_i_end_str.replace("Z", "+00:00")).astimezone(tz)
            else:
                ev_i_end = ev_i_start + timedelta(hours=1)
            for j in range(i + 1, len(sorted_evs)):
                ev_j_start, _ = sorted_evs[j]
                if ev_j_start < ev_i_end:
                    conflict_ids.add(ev_i.get("id", f"ev{i}"))
                    conflict_ids.add(sorted_evs[j][1].get("id", f"ev{j}"))

    lines = [f"Agenda {period}:", ""]
    for day_date in sorted(by_day.keys()):
        day_name = DAYS_ES[day_date.weekday()]
        month_name = MONTHS_ES[day_date.month - 1]
        lines.append(f"  {day_name} {day_date.day} de {month_name}:")
        for ev_start, ev in sorted(by_day[day_date], key=lambda x: x[0]):
            title = ev.get("summary", "Sin título")
            end_str = (ev.get("end", {}).get("dateTime") or ev.get("end", {}).get("date", ""))
            if "T" in end_str:
                ev_end = datetime.fromisoformat(end_str.replace("Z", "+00:00")).astimezone(tz)
                time_range = f"{ev_start.strftime('%H:%M')} - {ev_end.strftime('%H:%M')}"
            elif ev.get("start", {}).get("date"):
                time_range = "Todo el día"
            else:
                time_range = ev_start.strftime("%H:%M")
            marker = " ⚠ CONFLICTO" if ev.get("id", "") in conflict_ids else ""
            lines.append(f"    • {time_range}: {title}{marker}")
        lines.append("")

    if conflict_ids:
        lines.append("⚠ Atención: hay conflictos de horario en los eventos marcados.")

    return "\n".join(lines)


def has_explicit_scheduling_intent(text: str) -> bool:
    """Devuelve True solo si el texto expresa intención de agendar explícitamente."""
    normalized = (text or "").lower()
    has_schedule_verbs = bool(
        re.search(
            r"\b(fijar|fija|fijamos|programar|programa|programamos|reprogramar|reprograma|agendar|agendamos|convocar|convoca|convocamos|calendarizar|calendariza|calendarizamos|reservar|reserva|reservamos|reconfirmar|reconfirma|reconfirmamos|crear\s+(evento|cita|reuni[oó]n)|organizar|organiza|organizamos|coordinar|coordina|coordinamos|ponme|p[oó]nme|cr[eé]ame|crea|creame)\b|\b(organizar|organiza|organizamos|coordinar|coordina|coordinamos|reservar|reserva|reservamos)\s+(una\s+)?(reunion|reuni[oó]n|cita)\b|\b(dejarla\s+reservada|dejar\s+reservada|quedar\s+reservada|quede\s+reservada|quedar\s+la\s+reservada)\b",
            normalized,
        )
    )
    has_option_selection = bool(
        re.search(r"\b(opcion|opción)\b\s*(?:n[uú]mero\s*)?\d{1,2}\b", normalized)
    )
    has_document_context = bool(
        re.search(r"\b(documento|documentos|informe|presentar|adjunto|adjuntar|expediente)\b", normalized)
    )
    return (has_schedule_verbs or has_option_selection) and not has_document_context


def has_task_query_intent(text: str) -> bool:
    """Devuelve True si la petición es una consulta sobre tareas/acciones pendientes."""
    normalized = (text or "").lower()
    # Patrones directos e inequívocos de consulta de tareas
    direct_match = bool(
        re.search(
            r"\b(mis\s+tareas|lista\s+de\s+tareas|tareas\s+pendientes|acciones\s+pendientes|"
            r"qu[eé]\s+tengo\s+(que\s+hacer|pendiente)|qu[eé]\s+me\s+queda\s+(por\s+hacer|pendiente)|"
            r"qu[eé]\s+debo\s+hacer|tengo\s+algo\s+pendiente|hay\s+algo\s+pendiente|"
            r"recordatorios?\s+pendientes?|to.?do\s+list)\b",
            normalized,
        )
    )
    # 'pendiente/s' solo como sustantivo principal precedido de determinante/verbo
    pendiente_sustantivo = bool(
        re.search(r"\b(mis|los|las|hay|tengo|ver|mostrar|listar)\s+(tareas?\s+)?pendientes?\b", normalized)
    )
    # Excluir si el contexto trata de redactar, documentos o normativa
    exclude = bool(
        re.search(
            r"\b(documento|documentos|informe|pol[ií]tic|normativ|redact|escrib|email|correo|vacacion|teletrabaj)\b",
            normalized,
        )
    )
    return (direct_match or pendiente_sustantivo) and not exclude


def find_overlapping_events(calendar_service, start_dt: datetime, end_dt: datetime) -> list[dict]:
    """Busca eventos que se solapan con la franja solicitada en el calendario principal."""
    try:
        response = (
            calendar_service.events()
            .list(
                calendarId="primary",
                timeMin=start_dt.isoformat(),
                timeMax=end_dt.isoformat(),
                singleEvents=True,
                orderBy="startTime",
            )
            .execute()
        )
        return response.get("items", [])
    except Exception as e:
        logger.warning(f"No se pudo comprobar solapes en Calendar: {e}")
        return []

def _parse_calendar_event_datetime(value: dict, fallback_tz) -> datetime:
    """Convierte start/end de Google Calendar a datetime aware."""
    date_time = value.get("dateTime")
    if date_time:
        return datetime.fromisoformat(date_time.replace("Z", "+00:00"))

    # Evento de día completo (formato date). End en Google es exclusivo.
    date_only = value.get("date")
    if date_only:
        base = datetime.fromisoformat(date_only)
        return base.replace(tzinfo=fallback_tz)

    return datetime.now().astimezone()

def _merge_busy_intervals(intervals: list[tuple[datetime, datetime]]) -> list[tuple[datetime, datetime]]:
    if not intervals:
        return []

    intervals.sort(key=lambda x: x[0])
    merged = [intervals[0]]
    for current_start, current_end in intervals[1:]:
        last_start, last_end = merged[-1]
        if current_start <= last_end:
            merged[-1] = (last_start, max(last_end, current_end))
        else:
            merged.append((current_start, current_end))
    return merged


DEFAULT_TIME_PREFERENCES = {
    "preferred_start_hour": 10,
    "preferred_end_hour": 18,
    "avoid_monday_morning": True,
    "monday_morning_end_hour": 12,
    "avoid_friday_afternoon": True,
    "friday_afternoon_start_hour": 15,
    "avoid_weekends": True,
}


def _is_preferred_slot(start_dt: datetime, preferences: dict) -> bool:
    """Evalúa si un hueco encaja con preferencias horarias del usuario."""
    hour = start_dt.hour
    weekday = start_dt.weekday()  # lunes=0 ... domingo=6

    if preferences.get("avoid_weekends", False) and weekday >= 5:
        return False

    if hour < preferences["preferred_start_hour"] or hour >= preferences["preferred_end_hour"]:
        return False

    if preferences["avoid_monday_morning"] and weekday == 0 and hour < preferences["monday_morning_end_hour"]:
        return False

    if preferences["avoid_friday_afternoon"] and weekday == 4 and hour >= preferences["friday_afternoon_start_hour"]:
        return False

    return True

def find_next_available_slot(
    calendar_service,
    requested_start: datetime,
    requested_end: datetime,
    workday_start_hour: int = 8,
    workday_end_hour: int = 20,
    max_days_ahead: int = 7,
):
    """Busca el siguiente hueco libre con la misma duración (mismo día o próximos días)."""
    duration = requested_end - requested_start
    tz = requested_start.tzinfo

    for day_offset in range(0, max_days_ahead + 1):
        day_base = requested_start + timedelta(days=day_offset)
        day_start = day_base.replace(
            hour=workday_start_hour,
            minute=0,
            second=0,
            microsecond=0,
        )
        day_end = day_base.replace(
            hour=workday_end_hour,
            minute=0,
            second=0,
            microsecond=0,
        )

        try:
            response = (
                calendar_service.events()
                .list(
                    calendarId="primary",
                    timeMin=day_start.isoformat(),
                    timeMax=day_end.isoformat(),
                    singleEvents=True,
                    orderBy="startTime",
                )
                .execute()
            )
            events = response.get("items", [])
        except Exception as e:
            logger.warning(f"No se pudo buscar hueco alternativo: {e}")
            return None, None

        intervals: list[tuple[datetime, datetime]] = []
        for evt in events:
            if evt.get("status") == "cancelled":
                continue
            if evt.get("transparency") == "transparent":
                continue

            start_value = evt.get("start", {})
            end_value = evt.get("end", {})
            evt_start = _parse_calendar_event_datetime(start_value, tz)
            evt_end = _parse_calendar_event_datetime(end_value, tz)
            if evt_start.tzinfo is None:
                evt_start = evt_start.replace(tzinfo=tz)
            if evt_end.tzinfo is None:
                evt_end = evt_end.replace(tzinfo=tz)

            if evt_end <= day_start or evt_start >= day_end:
                continue
            intervals.append((max(evt_start, day_start), min(evt_end, day_end)))

        merged_busy = _merge_busy_intervals(intervals)
        cursor = max(requested_start, day_start) if day_offset == 0 else day_start

        for busy_start, busy_end in merged_busy:
            if cursor + duration <= busy_start:
                return cursor, cursor + duration
            if cursor < busy_end:
                cursor = busy_end

        if cursor + duration <= day_end:
            return cursor, cursor + duration

    return None, None


def find_available_slots(
    calendar_service,
    requested_start: datetime,
    requested_end: datetime,
    max_options: int = 3,
    workday_start_hour: int = 8,
    workday_end_hour: int = 20,
    max_days_ahead: int = 14,
    preferences: dict | None = None,
) -> list[tuple[datetime, datetime]]:
    """Propone varios huecos libres considerando preferencias horarias del usuario."""
    duration = requested_end - requested_start
    tz = requested_start.tzinfo
    prefs = preferences or DEFAULT_TIME_PREFERENCES
    day_search_start_hour = max(workday_start_hour, prefs.get("preferred_start_hour", workday_start_hour))

    preferred_slots: list[tuple[datetime, datetime]] = []
    fallback_slots: list[tuple[datetime, datetime]] = []

    for day_offset in range(0, max_days_ahead + 1):
        day_base = requested_start + timedelta(days=day_offset)
        if prefs.get("avoid_weekends", False) and day_base.weekday() >= 5:
            continue

        day_start = day_base.replace(hour=day_search_start_hour, minute=0, second=0, microsecond=0)
        day_end = day_base.replace(hour=workday_end_hour, minute=0, second=0, microsecond=0)

        try:
            response = (
                calendar_service.events()
                .list(
                    calendarId="primary",
                    timeMin=day_start.isoformat(),
                    timeMax=day_end.isoformat(),
                    singleEvents=True,
                    orderBy="startTime",
                )
                .execute()
            )
            events = response.get("items", [])
        except Exception as e:
            logger.warning(f"No se pudieron buscar opciones horarias alternativas: {e}")
            break

        intervals: list[tuple[datetime, datetime]] = []
        for evt in events:
            if evt.get("status") == "cancelled" or evt.get("transparency") == "transparent":
                continue

            evt_start = _parse_calendar_event_datetime(evt.get("start", {}), tz)
            evt_end = _parse_calendar_event_datetime(evt.get("end", {}), tz)

            if evt_start.tzinfo is None:
                evt_start = evt_start.replace(tzinfo=tz)
            if evt_end.tzinfo is None:
                evt_end = evt_end.replace(tzinfo=tz)

            if evt_end <= day_start or evt_start >= day_end:
                continue
            intervals.append((max(evt_start, day_start), min(evt_end, day_end)))

        merged_busy = _merge_busy_intervals(intervals)
        cursor = max(requested_start, day_start) if day_offset == 0 else day_start

        def register_candidate(start_candidate: datetime):
            end_candidate = start_candidate + duration
            candidate = (start_candidate, end_candidate)
            if _is_preferred_slot(start_candidate, prefs):
                preferred_slots.append(candidate)
            else:
                fallback_slots.append(candidate)

        for busy_start, busy_end in merged_busy:
            if cursor + duration <= busy_start:
                register_candidate(cursor)
            if cursor < busy_end:
                cursor = busy_end

            if len(preferred_slots) >= max_options:
                return preferred_slots[:max_options]

        if cursor + duration <= day_end:
            register_candidate(cursor)
            if len(preferred_slots) >= max_options:
                return preferred_slots[:max_options]

        if len(preferred_slots) + len(fallback_slots) >= max_options * 2:
            break

    combined = preferred_slots + fallback_slots
    combined.sort(key=lambda slot: slot[0])
    return combined[:max_options]

def create_calendar_event_from_text(calendar_service, event_text: str, email: dict):
    """Crea un evento en Calendar solo si detecta fecha explícita en el contenido."""
    event_text = (event_text or "").strip()
    if not event_text:
        return None

    latest_event_text = extract_latest_reply_content(event_text)
    raw_email_body = email.get("body", "")
    latest_email_body = extract_latest_reply_content(raw_email_body)
    selected_alternative_slot = parse_selected_alternative_slot(
        latest_reply_text=latest_email_body,
        raw_email_body=raw_email_body,
        reference_dt=datetime.now().astimezone(),
    )

    # Para fecha/hora priorizamos el cuerpo real del correo; el texto del clasificador
    # puede arrastrar contexto viejo del hilo y contaminar la extracción.
    prioritized_sources = [
        latest_email_body[:600],
        email.get("subject", ""),
        latest_event_text,
    ]
    source_text = "\n".join(prioritized_sources)
    if not has_explicit_scheduling_intent(source_text) and selected_alternative_slot is None:
        logger.info(
            "No se crea evento: el correo '%s' no expresa intención explícita de agendar",
            email.get("subject", "Sin asunto"),
        )
        return {"created": False, "conflict": False, "reason": "no_scheduling_intent"}

    start_dt, end_dt = _parse_event_datetimes_from_sources(
        prioritized_sources,
        datetime.now().astimezone(),
    )
    if (not start_dt or not end_dt) and selected_alternative_slot is not None:
        start_dt, end_dt = selected_alternative_slot

    if not start_dt or not end_dt:
        logger.info(
            "No se crea evento: no se detectó fecha explícita en el correo '%s'",
            email.get("subject", "Sin asunto"),
        )
        return {"created": False, "conflict": False, "reason": "no_date"}

    overlapping_events = find_overlapping_events(calendar_service, start_dt, end_dt)
    if overlapping_events:
        alternatives = find_available_slots(
            calendar_service,
            start_dt,
            end_dt,
            max_options=3,
            max_days_ahead=14,
        )
        top_conflicts = ", ".join(
            [evt.get("summary", "Sin título") for evt in overlapping_events[:3]]
        )
        if alternatives:
            first_start, first_end = alternatives[0]
            logger.warning(
                "Conflicto de agenda para '%s' (%s - %s). Solapa con: %s. "
                "Se propusieron %s opciones. Primera sugerida: %s - %s",
                email.get("subject", "Sin asunto"),
                start_dt.strftime("%Y-%m-%d %H:%M"),
                end_dt.strftime("%H:%M"),
                top_conflicts,
                len(alternatives),
                first_start.strftime("%Y-%m-%d %H:%M"),
                first_end.strftime("%H:%M"),
            )
        else:
            logger.warning(
                "Conflicto de agenda para '%s' (%s - %s). Solapa con: %s. "
                "No se encontraron huecos alternativos en el rango configurado.",
                email.get("subject", "Sin asunto"),
                start_dt.strftime("%Y-%m-%d %H:%M"),
                end_dt.strftime("%H:%M"),
                top_conflicts,
            )

        suggested_options = [
            {
                "start": option_start.isoformat(),
                "end": option_end.isoformat(),
                "duration_minutes": int((option_end - option_start).total_seconds() // 60),
            }
            for option_start, option_end in alternatives
        ]

        return {
            "created": False,
            "conflict": True,
            "reason": "overlap",
            "conflicts": overlapping_events,
            "start": start_dt.isoformat(),
            "end": end_dt.isoformat(),
            "suggested_start": suggested_options[0]["start"] if suggested_options else None,
            "suggested_end": suggested_options[0]["end"] if suggested_options else None,
            "suggested_options": suggested_options,
        }

    raw_subject = email.get("subject", "Reunión")
    summary = re.sub(
        r'^(?:organizar?|agenda[rn]?|programar?|crear?\s+(?:una?\s+)?(?:cita|reuni[oó]n|evento)|fijar?|concertar?|coordinar?|convocar?|reservar?)\s+(?:una?\s+)?',
        '',
        raw_subject.strip(),
        flags=re.IGNORECASE,
    ).strip()
    if not summary:
        summary = raw_subject
    summary = summary[:1].upper() + summary[1:]
    summary = summary[:120]
    # Enviamos dateTime con offset local explícito para evitar desplazamientos a UTC.
    event_body = {
        "summary": summary,
        "description": f"Creado automáticamente desde email.\n\n{event_text}",
        "start": {"dateTime": start_dt.isoformat()},
        "end": {"dateTime": end_dt.isoformat()},
    }

    try:
        event = calendar_service.events().insert(calendarId="primary", body=event_body).execute()
        logger.info(
            "Evento creado en Google Calendar: %s (%s)",
            event.get("summary", "Sin título"),
            event.get("htmlLink", "sin enlace"),
        )
        return {
            "created": True,
            "conflict": False,
            "event": event,
            "start": start_dt.isoformat(),
            "end": end_dt.isoformat(),
        }
    except Exception as e:
        logger.warning(f"No se pudo crear evento en Calendar: {e}")
        return {"created": False, "conflict": False, "reason": f"create_error: {e}"}

# ─── Procesamiento con CrewAI ──────────────────────────────────────────────

def procesar_email_con_crewai(email: dict) -> dict:
    """Procesa un email con SupervisorFlow."""
    sys.path.insert(0, str(Path(__file__).parent / "src"))
    from src.oficinacrew.main import SupervisorFlow

    # El asunto se incluye también en el cuerpo para mejorar la detección cuando el body está vacío
    body_content = email['body'].strip() or email['subject']
    prompt = f"""Email recibido:

De: {email['sender']}
Asunto: {email['subject']}

Cuerpo:
{body_content}
"""

    try:
        logger.info(f"Procesando email: {email['subject']}")
        flow = SupervisorFlow()
        result = None
        try:
            result = flow.kickoff(inputs={"peticion": prompt})
        except Exception as kickoff_err:
            # En Windows, CrewAI puede lanzar UnicodeEncodeError por los emojis del panel
            # de progreso. El flow puede haber completado correctamente igual: comprobamos
            # el state antes de propagar el error.
            if not flow.state.get("clasificacion"):
                raise kickoff_err
            logger.warning(f"Excepción tras kickoff (posible error de encoding en consola): {kickoff_err}")

        clasificacion = flow.state.get("clasificacion", {})
        resultado_documentos = flow.state.get("resultado_documentos", "")
        # Si kickoff falló pero el flow completó documentos, usar resultado_documentos como resumen
        if result is None and resultado_documentos:
            result = resultado_documentos
        analisis_email = analyze_email_urgency_and_actions(email)
        return {
            "success": True,
            "resumen": result,
            "resultado_documentos": resultado_documentos,
            "email_id": email["message_id"],
            "clasificacion": clasificacion,
            "analisis_email": analisis_email,
        }
    except Exception as e:
        logger.error(f"Error procesando email con CrewAI: {e}")
        return {
            "success": False,
            "error": str(e),
            "email_id": email["message_id"],
        }

# ─── State Management ──────────────────────────────────────────────────────

def cargar_state() -> dict:
    """Carga el estado del monitoreo (últimos emails procesados)."""
    if STATE_FILE.exists():
        with open(STATE_FILE, "r") as f:
            state = json.load(f)
            state.setdefault("ultimos_emails", [])
            state.setdefault("ultima_verificacion", None)
            state.setdefault("baseline_initialized", False)
            state.setdefault("baseline_unread_ids", [])
            return state
    return {
        "ultimos_emails": [],
        "ultima_verificacion": None,
        "baseline_initialized": False,
        "baseline_unread_ids": [],
    }

def guardar_state(state: dict):
    """Guarda el estado del monitoreo."""
    with open(STATE_FILE, "w") as f:
        json.dump(state, f, indent=2)


# ─── Procesamiento de email individual (reutilizable) ─────────────────────

def process_single_email_postactions(
    service,
    calendar_service,
    email: dict,
    resultado: dict,
    test_mode: bool = False,
    log_fn=None,
    event_fn=None,
):
    """
    Procesa las acciones post-análisis de un email (calendario, respuestas, borradores).

    Args:
        service: Gmail API service
        calendar_service: Calendar API service
        email: dict con datos del email (subject, sender, message_id, thread_id, etc.)
        resultado: dict devuelto por procesar_email_con_crewai()
        test_mode: si True, no envía emails ni modifica nada
        log_fn: callback para logging, firma: log_fn(msg: str)
        event_fn: callback para eventos UI, firma: event_fn(tipo: str, titulo: str, detalle: str)

    Returns:
        dict con resultado actualizado (puede incluir calendar_result, etc.)
    """
    def _log(msg):
        if log_fn:
            log_fn(msg)
        else:
            logger.info(msg)

    def _event(tipo, titulo, detalle):
        if event_fn:
            event_fn(tipo, titulo, detalle)

    if not resultado.get("success"):
        _log(f"⚠ Error: {resultado.get('error', '?')}")
        return resultado

    clasificacion = resultado.get("clasificacion", {})
    categoria = str(clasificacion.get("categoria", "")).strip().lower()
    subject = email.get("subject", "Sin asunto")
    sender = email.get("sender", "")
    sender_email = extract_email_address(sender)

    _log(f"→ Categoría: {categoria}")
    _event("email", f"Email procesado: {subject}", f"De: {sender} | Categoría: {categoria}")

    # ─── Agenda ───────────────────────────────────────────────────────────
    if categoria in {"agenda", "ambos"}:
        event_text = str(clasificacion.get("texto_agenda", "")).strip()
        if not event_text:
            event_text = f"Reunión sobre: {subject}"
        calendar_result = create_calendar_event_from_text(calendar_service, event_text, email)
        resultado["calendar_result"] = calendar_result

        # Confirmación de cita creada
        if calendar_result.get("created") and not calendar_result.get("conflict") and not test_mode:
            if sender_email:
                start_text = calendar_result.get("start")
                end_text = calendar_result.get("end")
                if start_text and end_text:
                    confirmation_text = (
                        "Hola,\n\n"
                        f"La cita ha quedado confirmada para el {datetime.fromisoformat(start_text).strftime('%d/%m/%Y a las %H:%M')} "
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
                        _log(f"→ Confirmación enviada a {sender_email}")
                        _event(
                            "confirmacion",
                            f"Reunión confirmada: {subject}",
                            f"{datetime.fromisoformat(start_text).strftime('%d/%m/%Y %H:%M')} - "
                            f"{datetime.fromisoformat(end_text).strftime('%H:%M')} → {sender_email}",
                        )
                    except Exception as e:
                        _log(f"⚠ Error enviando confirmación: {e}")

        # Conflicto de agenda → enviar alternativas
        if calendar_result.get("conflict") and not test_mode:
            add_label(service, email["message_id"], label_name="CrewAI-Conflicto-Agenda")
            suggested_options = calendar_result.get("suggested_options", [])
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
                    _log(f"→ Alternativas enviadas a {sender_email}")
                    _event(
                        "conflicto",
                        f"Conflicto de agenda: {subject}",
                        f"Se enviaron {len(suggested_options[:3])} opciones alternativas a {sender_email}",
                    )
                except Exception as e:
                    _log(f"⚠ Error enviando alternativas: {e}")

    # ─── Documentos → crear borrador ──────────────────────────────────────
    if categoria == "documentos":
        doc_output = str(resultado.get("resultado_documentos") or resultado.get("resumen", "")).strip()
        if sender_email and doc_output:
            if not test_mode:
                doc_reply_text = (
                    "Hola,\n\n"
                    "He procesado tu solicitud sobre documentos. "
                    "Te dejo el resumen preparado para revisión y envío:\n\n"
                    f"{doc_output}\n\n"
                    "Un saludo."
                )
                reply_body = build_reply_body(email, doc_reply_text)
                try:
                    draft = create_email_reply_draft(
                        service, sender_email,
                        email.get("subject", "Respuesta sobre documentos"),
                        reply_body,
                        thread_id=email.get("thread_id"),
                        in_reply_to=email.get("message_id_header"),
                        references=email.get("references_header") or email.get("message_id_header"),
                    )
                    _log(f"→ Borrador de documentos creado para {sender_email}")
                    _event(
                        "email",
                        f"Borrador creado: {subject}",
                        f"Resumen de documentos listo para revisión → {sender_email}",
                    )
                except Exception as e:
                    _log(f"⚠ Error creando borrador de documentos: {e}")
            else:
                _log("[TEST MODE] Se habría creado borrador de documentos")
        else:
            if not doc_output:
                _log("⚠ Resumen vacío, no se crea borrador")
            if not sender_email:
                _log("⚠ No se pudo extraer email del remitente")

    # Marcar como leído y etiquetar
    if not test_mode:
        mark_as_read(service, email["message_id"])
        add_label(service, email["message_id"])

    return resultado


# ─── Loop principal ───────────────────────────────────────────────────────

def monitor_loop(service, calendar_service, intervalo: int = 30, test_mode: bool = False, test_count: int = 2):
    """Loop principal de monitoreo."""
    state = cargar_state()
    logger.info(f"Gmail Monitor iniciado (intervalo: {intervalo}s, test_mode={test_mode})")

    if not state.get("baseline_initialized", False):
        baseline_ids = get_unread_message_ids(service)
        state["baseline_unread_ids"] = list(baseline_ids)
        state["baseline_initialized"] = True
        state["ultima_verificacion"] = datetime.now().isoformat()
        guardar_state(state)
        logger.info(
            f"Baseline inicial creado: {len(baseline_ids)} email(s) no leído(s) previos serán ignorados"
        )

    if test_mode:
        logger.info(f"[TEST MODE] Procesando últimos {test_count} emails...")

    try:
        while True:
            processed_count = 0
            skipped_count = 0
            # En modo test, incluir también emails ya leídos para poder reprocesar
            emails = get_new_emails(service, max_results=test_count if test_mode else 5, include_read=test_mode)
            baseline_unread_ids = set(state.get("baseline_unread_ids", []))

            if emails:
                logger.info(f"Encontrados {len(emails)} email(s) nuevo(s)")

                for email in emails:
                    # En modo test, no filtramos por emails ya procesados
                    if not test_mode:
                        # No procesar si ya fue procesado
                        if email["message_id"] in state["ultimos_emails"]:
                            skipped_count += 1
                            logger.debug(f"Email {email['subject']} ya fue procesado, saltando...")
                            continue

                        # Ignorar no leídos que ya existían cuando arrancó el monitor
                        if email["message_id"] in baseline_unread_ids:
                            skipped_count += 1
                            logger.debug(
                                f"Email {email['subject']} ya estaba no leído al iniciar, saltando..."
                            )
                            continue

                    logger.info(f"Procesando: {email['subject']} de {email['sender']}")

                    resultado = procesar_email_con_crewai(email)

                    if resultado["success"]:
                        processed_count += 1
                        logger.info(f"Email procesado correctamente")
                        clasificacion = resultado.get("clasificacion", {})
                        categoria = str(clasificacion.get("categoria", "")).strip().lower()
                        
                        # Mostrar resultado del flow en terminal
                        flow_result = str(resultado.get("resumen", "")).strip()
                        if flow_result:
                            print("\n" + "=" * 72)
                            print("RESULTADO DEL FLOW")
                            print("=" * 72)
                            print(flow_result)
                            print("=" * 72 + "\n")

                        analisis_email = resultado.get("analisis_email", {})
                        urgencia_val = analisis_email.get("urgencia", "no urgente")
                        justificacion_val = analisis_email.get("justificacion_urgencia", "")
                        acciones = analisis_email.get("acciones_pendientes", [])

                        # ── RF1 / RF3: Bloque prominente de urgencia y acciones ──
                        BOLD  = "\033[1m"
                        RED   = "\033[91m"
                        YEL   = "\033[93m"
                        GRN   = "\033[92m"
                        CYAN  = "\033[96m"
                        RESET = "\033[0m"

                        urg_color = RED if urgencia_val == "urgente" else (YEL if urgencia_val == "no urgente" else GRN)
                        print("\n" + "█" * 72)
                        print(f"  {BOLD}RF1 · CLASIFICACIÓN DE URGENCIA{RESET}")
                        print("█" * 72)
                        print(f"  Urgencia   : {urg_color}{BOLD}{urgencia_val.upper()}{RESET}")
                        print(f"  Justifica. : {justificacion_val}")
                        if acciones:
                            print(f"\n  {BOLD}RF3 · ACCIONES PENDIENTES{RESET}  ({len(acciones)} detectadas)")
                            for i, a in enumerate(acciones, 1):
                                partes = [f"  {i}. {CYAN}{a.get('accion','')}{RESET}"]
                                if a.get("responsable"):
                                    partes.append(f"     Responsable: {a['responsable']}")
                                if a.get("fecha_limite"):
                                    partes.append(f"     Fecha límite: {a['fecha_limite']}")
                                prio = a.get("prioridad", "media")
                                pc = RED if prio == "alta" else (YEL if prio == "media" else GRN)
                                partes.append(f"     Prioridad: {pc}{prio}{RESET}")
                                print("\n".join(partes))
                        else:
                            print(f"\n  {BOLD}RF3 · ACCIONES PENDIENTES{RESET}  : Ninguna detectada")
                        print("█" * 72 + "\n")

                        readable_summary = build_readable_summary(email, resultado)
                        print("=" * 72)
                        print("RESUMEN DE PROCESAMIENTO")
                        print("=" * 72)
                        print(readable_summary)
                        print("=" * 72 + "\n")

                        send_system_notification(
                            title=f"OficinaCrew: {email.get('subject', 'Sin asunto')}",
                            message=_truncate(readable_summary, 240),
                        )

                        if categoria in {"agenda", "ambos"}:
                            event_text = str(clasificacion.get("texto_agenda", "")).strip()
                            if not event_text:
                                event_text = f"Reunión sobre: {email['subject']}"
                            calendar_result = create_calendar_event_from_text(calendar_service, event_text, email)
                            resultado["calendar_result"] = calendar_result
                            sender_email = extract_email_address(email.get("sender", ""))
                            if calendar_result.get("created") and not calendar_result.get("conflict") and not test_mode:
                                if sender_email:
                                    start_text = calendar_result.get("start")
                                    end_text = calendar_result.get("end")
                                    if start_text and end_text:
                                        confirmation_text = (
                                            "Hola,\n\n"
                                            f"La cita ha quedado confirmada para el {datetime.fromisoformat(start_text).strftime('%d/%m/%Y a las %H:%M')} "
                                            f"hasta {datetime.fromisoformat(end_text).strftime('%H:%M')}.\n\n"
                                            "Queda reservada en mi agenda.\n\n"
                                            "Un saludo."
                                        )
                                        reply_body = build_reply_body(email, confirmation_text)
                                        try:
                                            send_email_reply(
                                                service,
                                                sender_email,
                                                email.get("subject", "Reunión"),
                                                reply_body,
                                                thread_id=email.get("thread_id"),
                                                in_reply_to=email.get("message_id_header"),
                                                references=email.get("references_header") or email.get("message_id_header"),
                                            )
                                            logger.info(
                                                "Confirmación enviada a %s para la cita %s - %s (thread: %s)",
                                                sender_email,
                                                start_text,
                                                end_text,
                                                email.get("thread_id", ""),
                                            )
                                        except Exception as e:
                                            logger.warning(f"No se pudo enviar confirmación de cita: {e}")

                            if calendar_result.get("conflict") and not test_mode:
                                # Aviso persistente en Gmail para seguimiento manual.
                                add_label(service, email["message_id"], label_name="CrewAI-Conflicto-Agenda")
                                suggested_options = calendar_result.get("suggested_options", [])
                                suggested_start = calendar_result.get("suggested_start")
                                suggested_end = calendar_result.get("suggested_end")

                                if sender_email and suggested_options:
                                    original_subject = email.get("subject", "Reunión")
                                    reply_subject = original_subject
                                    options_lines = []
                                    for idx, option in enumerate(suggested_options[:3], start=1):
                                        option_start = datetime.fromisoformat(option["start"])
                                        option_end = datetime.fromisoformat(option["end"])
                                        duration_minutes = option.get("duration_minutes", 60)
                                        options_lines.append(
                                            f"{idx}. {option_start.strftime('%d/%m/%Y a las %H:%M')} hasta "
                                            f"{option_end.strftime('%H:%M')} ({duration_minutes} min)"
                                        )

                                    suggestion_text = (
                                        "Hola,\n\n"
                                        "He revisado mi agenda y no tengo hueco en la franja solicitada.\n\n"
                                        "Te propongo estas opciones alternativas:\n"
                                        + "\n".join(options_lines)
                                        + "\n\n"
                                        "Si te encaja alguna, responde indicando el número de opción o el horario exacto y la dejo reservada.\n\n"
                                        "Un saludo."
                                    )
                                    reply_body = build_reply_body(email, suggestion_text)
                                    try:
                                        send_email_reply(
                                            service,
                                            sender_email,
                                            reply_subject,
                                            reply_body,
                                            thread_id=email.get("thread_id"),
                                            in_reply_to=email.get("message_id_header"),
                                            references=email.get("references_header") or email.get("message_id_header"),
                                        )
                                        logger.info(
                                            "Aviso enviado a %s con %s opciones alternativas (thread: %s)",
                                            sender_email,
                                            len(suggested_options[:3]),
                                            email.get("thread_id", ""),
                                        )
                                    except Exception as e:
                                        logger.warning(f"No se pudo enviar aviso de conflicto: {e}")
                                elif sender_email and suggested_start and suggested_end:
                                    # Fallback de compatibilidad por si no vienen opciones estructuradas.
                                    original_subject = email.get("subject", "Reunión")
                                    reply_subject = original_subject
                                    suggestion_text = (
                                        "Hola,\n\n"
                                        "He revisado mi agenda y no tengo hueco en la franja solicitada.\n\n"
                                        f"Puedo proponerte como alternativa el {datetime.fromisoformat(suggested_start).strftime('%d/%m/%Y a las %H:%M')} "
                                        f"hasta {datetime.fromisoformat(suggested_end).strftime('%H:%M')}.\n\n"
                                        "Si te encaja, puedo dejarla reservada.\n\n"
                                        "Un saludo."
                                    )
                                    reply_body = build_reply_body(email, suggestion_text)
                                    try:
                                        send_email_reply(
                                            service,
                                            sender_email,
                                            reply_subject,
                                            reply_body,
                                            thread_id=email.get("thread_id"),
                                            in_reply_to=email.get("message_id_header"),
                                            references=email.get("references_header") or email.get("message_id_header"),
                                        )
                                    except Exception as e:
                                        logger.warning(f"No se pudo enviar aviso de conflicto (fallback): {e}")

                        # Enviar respuesta por correo para categoría "documentos"
                        if categoria == "documentos":
                            sender_email = extract_email_address(email.get("sender", ""))
                            # Priorizar resultado_documentos del state; caer en resumen como fallback
                            doc_output = str(resultado.get("resultado_documentos") or resultado.get("resumen", "")).strip()
                            logger.info(f"[DOCUMENTOS] Resultado obtenido ({len(doc_output)} chars) para remitente '{sender_email}'")
                            if not doc_output:
                                logger.warning("[DOCUMENTOS] El resumen está vacío, no se crea borrador")
                            if not sender_email:
                                logger.warning("[DOCUMENTOS] No se pudo extraer email del remitente, no se crea borrador")
                            if sender_email and doc_output:
                                if not test_mode:
                                    doc_reply_text = (
                                        "Hola,\n\n"
                                        "He procesado tu solicitud sobre documentos. "
                                        "Te dejo el resumen preparado para revisión y envío:\n\n"
                                        f"{doc_output}\n\n"
                                        "Un saludo."
                                    )
                                    reply_body = build_reply_body(email, doc_reply_text)
                                    try:
                                        draft = create_email_reply_draft(
                                            service,
                                            sender_email,
                                            email.get("subject", "Respuesta sobre documentos"),
                                            reply_body,
                                            thread_id=email.get("thread_id"),
                                            in_reply_to=email.get("message_id_header"),
                                            references=email.get("references_header") or email.get("message_id_header"),
                                        )
                                        logger.info(
                                            "Borrador de respuesta de documentos creado para %s (draft_id: %s, thread: %s)",
                                            sender_email,
                                            draft.get("id", ""),
                                            email.get("thread_id", ""),
                                        )
                                    except Exception as e:
                                        logger.warning(f"No se pudo crear borrador de documentos: {e}")
                                else:
                                    logger.info("[TEST MODE] Se habría creado borrador de respuesta de documentos")

                        print_compact_status(email, resultado)
                        notification_title, notification_message = build_action_notification(email, resultado)
                        send_system_notification(notification_title, notification_message)

                        if not test_mode:
                            mark_as_read(service, email["message_id"])
                            add_label(service, email["message_id"])
                    else:
                        logger.warning(f"Error al procesar: {resultado.get('error')}")

                    # Guardar en state
                    state["ultimos_emails"].append(email["message_id"])
                    state["ultimos_emails"] = state["ultimos_emails"][-100:]  # Guardar últimos 100
                    state["ultima_verificacion"] = datetime.now().isoformat()
                    guardar_state(state)

            else:
                logger.debug("Sin emails nuevos")

            if test_mode:
                logger.info(
                    f"[TEST MODE] Finalizado. Procesados: {processed_count}; "
                    f"omitidos (ya procesados): {skipped_count}"
                )
                break

            logger.info(f"Siguiente verificación en {intervalo}s...")
            time.sleep(intervalo)

    except KeyboardInterrupt:
        logger.info("Monitor detenido por el usuario")
        sys.exit(0)
    except Exception as e:
        logger.error(f"Error en el loop: {e}")
        sys.exit(1)

# ─── CLI ──────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="Monitor de Gmail que dispara SupervisorFlow automáticamente",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Ejemplos:
  uv run python gmail_monitor.py                    # Escuchar en background
  uv run python gmail_monitor.py --intervalo 60    # Verificar cada 60 segundos
  uv run python gmail_monitor.py --test             # Test: procesar 2 últimos emails
        """,
    )
    parser.add_argument(
        "--intervalo",
        type=int,
        default=30,
        metavar="SEG",
        help="Intervalo entre verificaciones (default: 30s)",
    )
    parser.add_argument(
        "--test",
        action="store_true",
        help="Modo test: procesa últimos 2 emails y sale",
    )
    parser.add_argument(
        "--test-count",
        type=int,
        default=2,
        metavar="N",
        help="Número de emails a procesar en test mode (default: 2)",
    )

    args = parser.parse_args()

    try:
        service, calendar_service = get_google_services()
        monitor_loop(
            service,
            calendar_service,
            intervalo=args.intervalo,
            test_mode=args.test,
            test_count=args.test_count,
        )
    except Exception as e:
        logger.error(f"Error fatal: {e}")
        sys.exit(1)

if __name__ == "__main__":
    main()
