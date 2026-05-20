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
        from src.ejemplo1.main import llm

        prompt = f"""Analiza este email y responde SOLO con JSON válido.

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
      "fecha_limite": "fecha/hora si aparece, o vacío",
      "prioridad": "alta|media|baja"
    }}
  ]
}}

Reglas:
- No inventes datos que no estén en el email.
- Si no hay acciones, devuelve una lista vacía.
- La urgencia debe ser solo una de: urgente, no urgente, trivial.
"""
        response = llm.call(prompt)
        text = response if isinstance(response, str) else str(response)
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
                app_name="Ejemplo1 Gmail Monitor",
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

        # Intentar extraer el cuerpo
        body = ""
        if "parts" in message["payload"]:
            for part in message["payload"]["parts"]:
                if part["mimeType"] == "text/plain":
                    data = part["body"].get("data", "")
                    if data:
                        import base64
                        body = base64.urlsafe_b64decode(data).decode("utf-8")
                    break
        else:
            data = message["payload"]["body"].get("data", "")
            if data:
                import base64
                body = base64.urlsafe_b64decode(data).decode("utf-8")

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

def get_new_emails(service, max_results: int = 5) -> list[dict]:
    """Obtiene los últimos emails no leídos de la bandeja de entrada."""
    try:
        results = (
            service.users()
            .messages()
            .list(userId="me", q="is:unread", maxResults=max_results)
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

def has_explicit_scheduling_intent(text: str) -> bool:
    """Devuelve True solo si el texto expresa intención de agendar explícitamente."""
    normalized = (text or "").lower()
    has_schedule_verbs = bool(
        re.search(
            r"\b(fijar|fija|fijamos|programar|programa|programamos|reprogramar|reprograma|agendar|agenda|agendamos|convocar|convoca|convocamos|calendarizar|calendariza|calendarizamos|reservar|reserva|reservamos|reconfirmar|reconfirma|reconfirmamos|crear\s+(evento|cita)|organizar|organiza|organizamos|coordinar|coordina|coordinamos)\b|\b(organizar|organiza|organizamos|coordinar|coordina|coordinamos|reservar|reserva|reservamos)\s+(una\s+)?(reunion|reunión|cita)\b|\b(dejarla\s+reservada|dejar\s+reservada|quedar\s+reservada|quede\s+reservada|quedar\s+la\s+reservada)\b",
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

    summary = email.get("subject", "Reunión")[:120]
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
    from src.ejemplo1.main import SupervisorFlow

    prompt = f"""Email recibido:

De: {email['sender']}
Asunto: {email['subject']}

Cuerpo:
{email['body']}

---

Por favor:
1. Resume el contenido del email
2. Sugiere una respuesta automática apropiada (profesional pero concisa)
3. Categoriza el tipo de email (agenda, comunicacion, documentos, ambos)

Regla de prioridad:
- Si el email propone o fija una reunion/cita con fecha y/o hora y además requiere respuesta, usa ambos.
- Si solo contiene la reunion/cita y no requiere respuesta, usa agenda.
- Si la reunion es solo contexto (ej: "tengo reunion manana") pero la petición principal es de documentos, usa documentos.
- Frases de cortesia ("gracias", "quedo a la espera", "un saludo") no cambian la categoria por si solas.
"""

    try:
        logger.info(f"Procesando email: {email['subject']}")
        flow = SupervisorFlow()
        result = flow.kickoff(inputs={"peticion": prompt})
        clasificacion = flow.state.get("clasificacion", {})
        analisis_email = analyze_email_urgency_and_actions(email)
        return {
            "success": True,
            "resumen": result,
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
            emails = get_new_emails(service, max_results=test_count if test_mode else 5)
            baseline_unread_ids = set(state.get("baseline_unread_ids", []))

            if emails:
                logger.info(f"Encontrados {len(emails)} email(s) nuevo(s)")

                for email in emails:
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
                        analisis_email = resultado.get("analisis_email", {})
                        readable_summary = build_readable_summary(email, resultado)
                        print("\n" + "=" * 72)
                        print("RESUMEN DE PROCESAMIENTO")
                        print("=" * 72)
                        print(readable_summary)
                        print("=" * 72 + "\n")
                        logger.info(
                            "Urgencia: %s | Justificación: %s",
                            analisis_email.get("urgencia", "no urgente"),
                            analisis_email.get("justificacion_urgencia", ""),
                        )
                        send_system_notification(
                            title=f"Ejemplo1: {email.get('subject', 'Sin asunto')}",
                            message=_truncate(readable_summary, 240),
                        )
                        acciones = analisis_email.get("acciones_pendientes", [])
                        if acciones:
                            logger.info("Acciones pendientes detectadas: %s", len(acciones))
                            for i, accion in enumerate(acciones, start=1):
                                logger.info(
                                    "  %s) %s | responsable=%s | fecha_limite=%s | prioridad=%s",
                                    i,
                                    accion.get("accion", ""),
                                    accion.get("responsable", ""),
                                    accion.get("fecha_limite", ""),
                                    accion.get("prioridad", "media"),
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
