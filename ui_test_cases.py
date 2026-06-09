"""Casos manuales para validar requisitos desde la UI web.

Cada requisito incluye:
- objetivo: que se quiere comprobar manualmente
- casos: prompts de ejemplo para lanzar desde la interfaz
- esperado: que deberias observar en la UI o en el sistema
"""

UI_REQUIREMENT_CASES = [
    {
        "requisito": "RF1",
        "titulo": "Clasificacion de emails por urgencia",
        "objetivo": "Comprobar que peticiones tipo email se clasifican por urgencia y justifican la decision.",
        "casos": [
            {
                "prompt": "Analiza este email: Asunto: URGENTE. Cuerpo: El servidor de produccion esta caido, necesitamos solucion ASAP hoy antes de las 18:00.",
                "esperado": "Debe detectar urgencia alta o contenido claramente critico, con justificacion breve.",
            },
            {
                "prompt": "Analiza este email: Asunto: Actualizacion semanal. Cuerpo: El proyecto Alpha avanza segun lo previsto y no requiere accion inmediata.",
                "esperado": "Debe clasificarlo como no urgente o trivial, nunca como urgente.",
            },
        ],
    },
    {
        "requisito": "RF3",
        "titulo": "Extraccion de acciones pendientes",
        "objetivo": "Comprobar que se extraen tareas, responsables, fechas limite y prioridad cuando aparecen en el mensaje.",
        "casos": [
            {
                "prompt": "Analiza este email: Necesito tres cosas: enviame el contrato firmado, confirma la asistencia a la reunion del martes y prepara la presentacion para el cliente antes del viernes.",
                "esperado": "Deben aparecer varias acciones pendientes estructuradas.",
            },
            {
                "prompt": "Analiza este email: Te informo de que la reunion del jueves se ha cancelado. No hace falta hacer nada.",
                "esperado": "No deberia inventar acciones pendientes.",
            },
        ],
    },
    {
        "requisito": "RF4",
        "titulo": "Sugerencia y creacion de horarios",
        "objetivo": "Comprobar que la UI crea eventos o propone alternativas cuando detecta una peticion de agenda.",
        "casos": [
            {
                "prompt": "Creame una cita manana a las 13pm con el Festival de les Arts",
                "esperado": "Debe crear el evento en Calendar si no hay conflicto y mostrar 'evento creado'.",
            },
            {
                "prompt": "Ponme una reunion el viernes a las 10:00 con marketing",
                "esperado": "Debe crear la reunion o detectar conflicto y sugerir alternativas.",
            },
            {
                "prompt": "Reserva el lunes de 9 a 10 para una sesion de formacion interna",
                "esperado": "Debe interpretar la franja horaria y crear el evento con una hora de duracion o la franja indicada.",
            },
        ],
    },
    {
        "requisito": "RF5",
        "titulo": "Resumen de agenda",
        "objetivo": "Comprobar que consultas sobre agenda devuelven un resumen por rango de fechas y marcan conflictos si existen.",
        "casos": [
            {
                "prompt": "Que citas tengo esta semana",
                "esperado": "Debe mostrar un resumen de agenda semanal, no intentar crear una cita nueva.",
            },
            {
                "prompt": "Hazme un resumen de mi semana",
                "esperado": "Debe devolver un resumen agrupado por dia.",
            },
            {
                "prompt": "Tengo algo manana",
                "esperado": "Debe devolver los eventos de manana o indicar que no hay eventos.",
            },
            {
                "prompt": "Tengo algo el viernes a las 10 de la manana",
                "esperado": "Debe consultar la agenda, no crear un evento.",
            },
        ],
    },
    {
        "requisito": "RF6",
        "titulo": "Busqueda de documentos",
        "objetivo": "Comprobar manualmente el estado actual del requisito en la UI cuando se busquen documentos.",
        "casos": [
            {
                "prompt": "Busca documentos sobre vacaciones",
                "esperado": "Debe localizar documentos relacionados o evidenciar la limitacion actual si aun no esta cerrado.",
            },
        ],
    },
    {
        "requisito": "RF7",
        "titulo": "Resumen de documentos",
        "objetivo": "Comprobar manualmente el resumen de documentos desde la UI.",
        "casos": [
            {
                "prompt": "Resume el documento politica_teletrabajo.md",
                "esperado": "Debe devolver un resumen coherente del documento.",
            },
            {
                "prompt": "Hazme un resumen de la empresa Erasmus & Co",
                "esperado": "Debe buscar el documento asociado y generar el resumen correspondiente.",
            },
        ],
    },
    {
        "requisito": "RF8",
        "titulo": "Extraccion de informacion especifica",
        "objetivo": "Comprobar manualmente preguntas concretas sobre documentos.",
        "casos": [
            {
                "prompt": "Que dice la politica de vacaciones sobre los dias consecutivos permitidos",
                "esperado": "Debe responder a la pregunta concreta usando el documento adecuado o mostrar la limitacion actual.",
            },
        ],
    },
]
