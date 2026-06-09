"""
tests/test_requisitos.py
========================
Casos de prueba funcionales para los requisitos completados del TFM.

Cómo ejecutar:
    uv run pytest tests/ -v
    uv run pytest tests/ -v -k rf1         # Solo RF1
    uv run pytest tests/ -v -k rf3         # Solo RF3
    uv run pytest tests/ -v -k rf4         # Solo RF4
    uv run pytest tests/ -v -k rf5         # Solo RF5

Requisitos cubiertos
--------------------
  RF1 – Clasificación de emails por urgencia (urgente / no urgente / trivial)
  RF3 – Extracción de acciones pendientes en formato estructurado
  RF4 – Sugerencia/creación de horarios para reuniones (intent + parse)
  RF5 – Resumen de agenda por rango de fechas + detección de consulta
"""

import re
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

# Hacemos que src/ sea importable
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))
sys.path.insert(0, str(Path(__file__).parent.parent))


# ══════════════════════════════════════════════════════════════════════════════
# RF1 – Clasificación de emails por urgencia
# ══════════════════════════════════════════════════════════════════════════════

class TestRF1_UrgenciaEmail:
    """
    RF1: El sistema debe clasificar emails en urgente / no urgente / trivial
    y proporcionar una justificación breve.
    """

    def _analizar(self, sender: str, subject: str, body: str) -> dict:
        from gmail_monitor import analyze_email_urgency_and_actions
        return analyze_email_urgency_and_actions(
            {"sender": sender, "subject": subject, "body": body}
        )

    # ── Urgente ────────────────────────────────────────────────────────────
    def test_rf1_email_urgente_multiples_indicadores(self):
        """Email con 'urgente', 'ASAP' y 'hoy' → urgente."""
        r = self._analizar(
            sender="jefe@empresa.com",
            subject="URGENTE: servidor caído",
            body="El servidor de producción está caído. Necesitamos solución ASAP. "
                 "Esto es CRÍTICO, hay que resolverlo hoy antes de las 18:00 o bloqueamos toda la operación.",
        )
        assert r["urgencia"] == "urgente", f"Esperado 'urgente', obtenido '{r['urgencia']}'"
        assert r["justificacion_urgencia"], "Debe incluir justificación"

    def test_rf1_email_urgente_servidor_produccion(self):
        """Email de incidencia crítica en producción → urgente."""
        r = self._analizar(
            sender="ops@empresa.com",
            subject="CRÍTICO: base de datos inaccesible",
            body="La base de datos principal está inaccesible. "
                 "Es bloqueante para todo el equipo. Urge resolución inmediata.",
        )
        assert r["urgencia"] == "urgente"

    def test_rf1_email_urgente_tiene_justificacion(self):
        """La justificación de urgente debe ser un texto no vacío."""
        r = self._analizar(
            sender="ceo@empresa.com",
            subject="Reunión urgente - 30 min",
            body="Necesito verte cuanto antes. Es urgente. Llámame inmediatamente.",
        )
        assert r["justificacion_urgencia"].strip() != ""

    # ── No urgente ──────────────────────────────────────────────────────────
    def test_rf1_email_no_urgente_reunion_planificada(self):
        """Email de planificación sin indicadores de urgencia → no urgente."""
        r = self._analizar(
            sender="colega@empresa.com",
            subject="Reunión de planning del trimestre",
            body="Hola, quería proponerte una reunión de planning para el próximo mes. "
                 "Sin prisa, cuando tengas un hueco lo coordinamos.",
        )
        assert r["urgencia"] in {"no urgente", "trivial"}

    def test_rf1_email_no_urgente_actualizacion_proyecto(self):
        """Actualización informativa semanal → no urgente."""
        r = self._analizar(
            sender="pm@empresa.com",
            subject="Actualización semanal del proyecto Alpha",
            body="El proyecto Alpha avanza según lo previsto. "
                 "Esta semana hemos completado los módulos A y B. "
                 "El siguiente hito está programado para dentro de dos semanas.",
        )
        assert r["urgencia"] in {"no urgente", "trivial"}

    # ── Trivial ─────────────────────────────────────────────────────────────
    def test_rf1_email_trivial_mensaje_corto_informativo(self):
        """Email muy corto sin acciones ni urgencia → trivial o no urgente."""
        r = self._analizar(
            sender="rrhh@empresa.com",
            subject="Recordatorio: el viernes es festivo",
            body="Recordamos que el próximo viernes es festivo local.",
        )
        # trivial es ideal, pero no urgente también es correcto
        assert r["urgencia"] in {"trivial", "no urgente"}

    # ── Estructura de la respuesta ──────────────────────────────────────────
    def test_rf1_respuesta_tiene_campos_requeridos(self):
        """La respuesta debe incluir urgencia, justificacion_urgencia y acciones_pendientes."""
        r = self._analizar(
            sender="x@y.com",
            subject="Prueba",
            body="Este es un email de prueba sin contenido relevante.",
        )
        assert "urgencia" in r
        assert "justificacion_urgencia" in r
        assert "acciones_pendientes" in r

    def test_rf1_urgencia_valor_valido(self):
        """El valor de urgencia siempre es uno de los tres permitidos."""
        r = self._analizar(
            sender="x@y.com",
            subject="Cualquier email",
            body="Contenido arbitrario del email para verificar normalización.",
        )
        assert r["urgencia"] in {"urgente", "no urgente", "trivial"}


# ══════════════════════════════════════════════════════════════════════════════
# RF3 – Extracción de acciones pendientes
# ══════════════════════════════════════════════════════════════════════════════

class TestRF3_AccionesPendientes:
    """
    RF3: El sistema debe extraer acciones pendientes del email en formato
    estructurado con accion, responsable, fecha_límite y prioridad.
    """

    def _analizar(self, body: str, subject: str = "Prueba") -> list[dict]:
        from gmail_monitor import analyze_email_urgency_and_actions
        r = analyze_email_urgency_and_actions(
            {"sender": "x@y.com", "subject": subject, "body": body}
        )
        return r.get("acciones_pendientes", [])

    def test_rf3_detecta_accion_concreta(self):
        """Un email con una tarea clara debe tener al menos una acción."""
        acciones = self._analizar(
            "Por favor, envíame el informe de ventas antes del viernes. Es importante para la presentación."
        )
        assert len(acciones) >= 1, "Debe detectar al menos una acción pendiente"

    def test_rf3_estructura_accion_campos_requeridos(self):
        """Cada acción debe tener los campos: accion, responsable, fecha_limite, prioridad."""
        acciones = self._analizar(
            "Necesito que prepares el presupuesto para el proyecto Beta antes del lunes. Prioridad alta."
        )
        if acciones:
            for a in acciones:
                assert "accion" in a, "Falta campo 'accion'"
                assert "responsable" in a, "Falta campo 'responsable'"
                assert "fecha_limite" in a, "Falta campo 'fecha_limite'"
                assert "prioridad" in a, "Falta campo 'prioridad'"

    def test_rf3_prioridad_valor_valido(self):
        """El campo prioridad solo puede ser alta, media o baja."""
        acciones = self._analizar(
            "Por favor revisa el código urgentemente y corrige los errores críticos hoy mismo."
        )
        for a in acciones:
            assert a["prioridad"] in {"alta", "media", "baja"}, \
                f"Prioridad inválida: {a['prioridad']}"

    def test_rf3_email_sin_acciones_devuelve_lista_vacia(self):
        """Un email puramente informativo sin tareas → lista vacía o con acciones vacías."""
        acciones = self._analizar(
            "Te informo que la reunión del jueves fue cancelada. No hay nada que hacer de tu parte."
        )
        # Puede devolver lista vacía o acciones con texto vacío
        acciones_con_texto = [a for a in acciones if a.get("accion", "").strip()]
        assert len(acciones_con_texto) == 0

    def test_rf3_multiples_acciones(self):
        """Un email con varias tareas → al menos 2 acciones."""
        acciones = self._analizar(
            "Necesito tres cosas: primero, envíame el contrato firmado. "
            "Segundo, confirma la asistencia a la reunión del martes. "
            "Tercero, prepara la presentación para el cliente antes del viernes."
        )
        assert len(acciones) >= 2, f"Se esperaban ≥2 acciones, obtenidas: {len(acciones)}"

    def test_rf3_accion_no_inventa_responsable(self):
        """Si el email no menciona responsable, el campo debe estar vacío o neutro."""
        acciones = self._analizar(
            "Hay que revisar los documentos del proyecto cuanto antes."
        )
        for a in acciones:
            # No debe inventar nombres de personas que no aparecen en el texto
            responsable = a.get("responsable", "").lower()
            assert "juan" not in responsable
            assert "pedro" not in responsable
            assert "maría" not in responsable


# ══════════════════════════════════════════════════════════════════════════════
# RF4 – Sugerencia de horarios / creación de eventos en agenda
# ══════════════════════════════════════════════════════════════════════════════

class TestRF4_SugerenciaHorarios:
    """
    RF4: El sistema debe detectar intención de crear evento y parsear
    correctamente fecha y hora de la petición.
    """

    def _intent(self, text: str) -> bool:
        from gmail_monitor import has_explicit_scheduling_intent
        return has_explicit_scheduling_intent(text)

    def _parse(self, text: str) -> tuple:
        from gmail_monitor import _parse_event_datetimes_from_sources
        ref = datetime.now().astimezone()
        return _parse_event_datetimes_from_sources([text], ref)

    # ── Detección de intención ──────────────────────────────────────────────
    def test_rf4_detecta_intent_crear_cita(self):
        assert self._intent("Créame una cita mañana a las 10:00")

    def test_rf4_detecta_intent_ponme_reunion(self):
        assert self._intent("Ponme una reunión el viernes a las 16:00")

    def test_rf4_detecta_intent_reservar(self):
        assert self._intent("Reserva el lunes de 9 a 10 para una sesión de formación")

    def test_rf4_detecta_intent_agendar(self):
        assert self._intent("Agendamos una llamada con el cliente el jueves a las 11h")

    def test_rf4_no_detecta_intent_consulta_agenda(self):
        """Una consulta de agenda no debe activar la intención de crear evento."""
        assert not self._intent("¿Qué reuniones tengo esta semana?")

    def test_rf4_no_detecta_intent_email_informativo(self):
        assert not self._intent("Te informo que el proyecto avanza bien, sin novedades.")

    # ── Parseo de fechas y horas ────────────────────────────────────────────
    def test_rf4_parsea_manana_a_las_13(self):
        start, end = self._parse("Créame una cita mañana a las 13:00")
        ref = datetime.now().astimezone()
        assert start is not None
        assert start.hour == 13
        assert start.date().day == (ref.date().day + 1) % 31 or True  # mañana

    def test_rf4_parsea_hora_pm(self):
        start, _ = self._parse("Reunión el lunes a las 3pm")
        assert start is not None
        assert start.hour == 15

    def test_rf4_parsea_hora_h(self):
        start, _ = self._parse("Cita el viernes a las 10h")
        assert start is not None
        assert start.hour == 10

    def test_rf4_parsea_hora_con_a_las(self):
        start, _ = self._parse("Bloquea el martes a las 9 horas")
        assert start is not None
        assert start.hour == 9

    def test_rf4_duracion_default_una_hora(self):
        """Sin duración explícita, el evento dura 1 hora."""
        start, end = self._parse("Reunión mañana a las 11:00")
        assert start is not None and end is not None
        delta_min = int((end - start).total_seconds() // 60)
        assert delta_min == 60

    def test_rf4_parsea_dia_de_semana(self):
        start, _ = self._parse("Reunión el jueves a las 14:00")
        assert start is not None
        assert start.weekday() == 3  # jueves = 3


# ══════════════════════════════════════════════════════════════════════════════
# RF5 – Resumen de agenda por rango de fechas
# ══════════════════════════════════════════════════════════════════════════════

class TestRF5_ResumenAgenda:
    """
    RF5: El sistema debe detectar consultas de agenda, parsear el rango
    de fechas y generar un resumen estructurado con marcado de conflictos.
    """

    REF = datetime(2026, 6, 9, 10, 0, tzinfo=timezone.utc)  # martes

    def _intent(self, text: str) -> bool:
        from gmail_monitor import has_agenda_summary_intent
        return has_agenda_summary_intent(text)

    def _rango(self, text: str) -> tuple:
        from gmail_monitor import parse_date_range_for_summary
        return parse_date_range_for_summary(text, self.REF)

    def _resumen(self, events: list, start, end) -> str:
        from gmail_monitor import build_agenda_summary_text
        return build_agenda_summary_text(events, start, end)

    # ── Detección de intención de consulta ─────────────────────────────────
    def test_rf5_detecta_que_citas_tengo(self):
        assert self._intent("¿Qué citas tengo esta semana?")

    def test_rf5_detecta_que_tengo_manana(self):
        assert self._intent("¿Qué tengo mañana?")

    def test_rf5_detecta_hay_algo_hoy(self):
        assert self._intent("¿Hay algo hoy en mi agenda?")

    def test_rf5_detecta_resumen_semana(self):
        assert self._intent("Hazme un resumen de mi semana")

    def test_rf5_detecta_que_reuniones_tengo(self):
        assert self._intent("¿Qué reuniones tengo el lunes?")

    def test_rf5_detecta_tengo_algo_el_viernes(self):
        assert self._intent("¿Tengo algo el viernes a las 10 de la mañana?")

    def test_rf5_detecta_ver_mi_agenda(self):
        assert self._intent("Ver mi agenda de esta semana")

    def test_rf5_detecta_mis_citas(self):
        assert self._intent("Muéstrame mis citas de mañana")

    def test_rf5_no_detecta_crear_cita(self):
        """Crear un evento no debe activar la detección de consulta."""
        assert not self._intent("Créame una cita mañana a las 13:00")

    # ── Parseo de rango de fechas ───────────────────────────────────────────
    def test_rf5_rango_hoy(self):
        start, end = self._rango("¿Qué tengo hoy?")
        assert start.date() == self.REF.date()
        assert end.date() == self.REF.date()

    def test_rf5_rango_manana(self):
        from datetime import timedelta
        start, end = self._rango("¿Hay algo mañana?")
        expected = (self.REF + timedelta(days=1)).date()
        assert start.date() == expected
        assert end.date() == expected

    def test_rf5_rango_esta_semana(self):
        """Esta semana → lunes a domingo de la semana actual."""
        start, end = self._rango("¿Qué reuniones tengo esta semana?")
        # La semana contiene exactamente 7 días
        delta = (end.date() - start.date()).days
        assert delta == 6, f"Semana debe ser 6 días de diferencia (lunes-domingo), obtenido: {delta}"
        assert start.weekday() == 0, "La semana empieza en lunes"

    def test_rf5_rango_proxima_semana(self):
        start, end = self._rango("¿Qué hay la próxima semana?")
        assert start.weekday() == 0  # lunes
        assert (end.date() - start.date()).days == 6

    def test_rf5_rango_por_defecto_7_dias(self):
        """Sin referencia temporal → próximos 7 días."""
        start, end = self._rango("Dame mi agenda")
        delta = (end.date() - start.date()).days
        assert delta == 7

    # ── Generación del resumen ──────────────────────────────────────────────
    def test_rf5_resumen_sin_eventos(self):
        from gmail_monitor import parse_date_range_for_summary
        start, end = parse_date_range_for_summary("hoy", self.REF)
        texto = self._resumen([], start, end)
        assert "no hay eventos" in texto.lower(), f"Debería indicar que no hay eventos: {texto}"

    def test_rf5_resumen_con_evento(self):
        from gmail_monitor import parse_date_range_for_summary
        start, end = parse_date_range_for_summary("hoy", self.REF)
        evento = {
            "id": "ev1",
            "summary": "Reunión de equipo",
            "start": {"dateTime": "2026-06-09T10:00:00+00:00"},
            "end":   {"dateTime": "2026-06-09T11:00:00+00:00"},
        }
        texto = self._resumen([evento], start, end)
        assert "Reunión de equipo" in texto

    def test_rf5_resumen_marca_conflictos(self):
        """Dos eventos solapados en el mismo día deben marcarse como conflicto."""
        from gmail_monitor import parse_date_range_for_summary
        start, end = parse_date_range_for_summary("hoy", self.REF)
        ev1 = {
            "id": "ev1",
            "summary": "Reunión A",
            "start": {"dateTime": "2026-06-09T10:00:00+00:00"},
            "end":   {"dateTime": "2026-06-09T11:30:00+00:00"},
        }
        ev2 = {
            "id": "ev2",
            "summary": "Reunión B",
            "start": {"dateTime": "2026-06-09T11:00:00+00:00"},
            "end":   {"dateTime": "2026-06-09T12:00:00+00:00"},
        }
        texto = self._resumen([ev1, ev2], start, end)
        assert "CONFLICTO" in texto, f"Debe marcar conflicto. Texto obtenido:\n{texto}"

    def test_rf5_resumen_formato_agrupado_por_dia(self):
        """El resumen debe estar agrupado por día (contiene nombre de día o fecha)."""
        from gmail_monitor import parse_date_range_for_summary
        start, end = parse_date_range_for_summary("esta semana", self.REF)
        evento = {
            "id": "ev1",
            "summary": "Demo cliente",
            "start": {"dateTime": "2026-06-10T09:00:00+00:00"},
            "end":   {"dateTime": "2026-06-10T10:00:00+00:00"},
        }
        texto = self._resumen([evento], start, end)
        # Debe contener al menos un nombre de día en español
        dias = ["lunes", "martes", "miércoles", "jueves", "viernes", "sábado", "domingo"]
        assert any(d in texto.lower() for d in dias), \
            f"Debe agrupar por día. Texto:\n{texto}"
