"""
Casos de prueba para el benchmark del sistema multi-agente SupervisorFlow.

Cada caso define:
  - peticion      : texto que recibe el supervisor
  - categoria     : categoría esperada ("agenda" | "comunicacion" | "documentos" | "ambos")
  - descripcion   : etiqueta corta para los informes
  - checks_salida : lista de strings que DEBEN aparecer en la salida del crew (insensible a mayúsculas)
"""

CASOS_CLASIFICACION = [
    # ─── AGENDA ────────────────────────────────────────────────────────────────
    {
        "peticion": "Organiza una reunión con el equipo de marketing para el viernes a las 10:00.",
        "categoria": "agenda",
        "descripcion": "agenda_reunion_marketing",
        "checks_salida": ["viernes", "10"],
    },
    {
        "peticion": "Programa una cita con el médico para el lunes por la tarde.",
        "categoria": "agenda",
        "descripcion": "agenda_cita_medico",
        "checks_salida": ["lunes"],
    },
    {
        "peticion": "Bloquea el miércoles de 14:00 a 16:00 para una sesión de formación interna.",
        "categoria": "agenda",
        "descripcion": "agenda_bloqueo_formacion",
        "checks_salida": ["miércoles", "14", "16"],
    },
    # ─── COMUNICACION ───────────────────────────────────────────────────────────
    {
        "peticion": "Redacta un email al cliente de Telefónica informando del retraso en la entrega.",
        "categoria": "comunicacion",
        "descripcion": "com_email_retraso_telefonica",
        "checks_salida": ["telefónica", "retraso"],
    },
    {
        "peticion": "Escribe un mensaje de disculpa al proveedor por el pago tardío.",
        "categoria": "comunicacion",
        "descripcion": "com_mensaje_disculpa_proveedor",
        "checks_salida": ["pago", "disculpa"],
    },
    {
        "peticion": "Redacta un comunicado interno anunciando las nuevas políticas de teletrabajo.",
        "categoria": "comunicacion",
        "descripcion": "com_comunicado_teletrabajo",
        "checks_salida": ["teletrabajo"],
    },
    # ─── DOCUMENTOS ────────────────────────────────────────────────────────────
    {
        "peticion": "¿Qué dice el contrato sobre las penalizaciones por incumplimiento?",
        "categoria": "documentos",
        "descripcion": "doc_contrato_penalizaciones",
        "checks_salida": [],
    },
    {
        "peticion": "Extrae las cláusulas de confidencialidad del acuerdo NDA.",
        "categoria": "documentos",
        "descripcion": "doc_clausulas_nda",
        "checks_salida": [],
    },
    {
        "peticion": "Busca en los documentos disponibles información sobre el presupuesto del proyecto.",
        "categoria": "documentos",
        "descripcion": "doc_busqueda_presupuesto",
        "checks_salida": [],
    },
    # ─── AMBOS ─────────────────────────────────────────────────────────────────
    {
        "peticion": "Convoca una reunión con el equipo de DevOps para el jueves y redacta el email de invitación.",
        "categoria": "ambos",
        "descripcion": "ambos_reunion_email_devops",
        "checks_salida": ["jueves"],
    },
    {
        "peticion": "Agenda una llamada con el director financiero el martes a las 9:00 "
                    "y envía un email confirmando la cita.",
        "categoria": "ambos",
        "descripcion": "ambos_llamada_email_director",
        "checks_salida": ["martes", "9"],
    },
]
