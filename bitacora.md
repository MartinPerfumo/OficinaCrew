# Bitácora del Proyecto

## 2026-05-07 (Previo al 19)
- Commit inicial del proyecto: sistema multi-agente con Supervisor Flow en CrewAI.
- Estructura base creada: configuración del proyecto, dependencias y lockfile (`pyproject.toml`, `uv.lock`, `.env.example`, `.gitignore`).
- Documentación inicial añadida (`README.md`, `AGENTS.md`).
- Implementación inicial de arquitectura por crews:
  - Agenda crew (agentes + tareas YAML + código).
  - Comunicación crew (agentes + tareas YAML + código).
- Flujo supervisor inicial en `src/ejemplo1/main.py`.
- Módulo de tools base preparado (`src/ejemplo1/tools/`).

## 2026-05-19
- Se estabilizó el entorno: resolución de bloqueos de uv/.venv e instalación de dependencias Google faltantes.
- Se integró Calendar en el monitor y setup OAuth con scopes de Gmail + calendar.events.
- Se añadió baseline de no leídos + estado persistente para evitar reprocesar correos antiguos.
- Se implementó creación de eventos desde correo, detección de solapes y propuesta de hueco alternativo.
- Se activó respuesta automática por conflicto y confirmación automática cuando la cita se reserva.
- Se reforzó parser de fecha/hora en español (13hs, 13:30h, y media, y cuarto, etc.) y correcciones de timezone.
- Se mejoró clasificación para evitar falsos positivos: correos de documentos con mención contextual de reunión ya no generan cita.
- Se añadió salida legible/compacta y notificaciones del sistema.

## 2026-05-20
- Se corrigió cierre con excepción de telemetría/hilos en Python 3.13 desactivando telemetry de CrewAI al inicio.
- Se arregló parsing en respuestas de hilo: limpieza de texto citado para evitar capturar horas antiguas (ej. 18:57).
- Se corrigió semántica de fechas en lenguaje natural:
  - "viernes que viene" => viernes inmediato
  - "de la mañana" no se interpreta como "mañana" (día siguiente)
  - "mañana", "mañana por la mañana" y "mañana por la tarde" funcionan correctamente
  - soporte para "el día 21"
- Se ajustó clasificación para citas puras: no activar comunicación por cortesías ("quedo a la espera", "gracias").
- Se amplió intención de agenda para verbos de reserva ("reserva", "reservar", etc.).
- Se implementó RF4 en sugerencias de horario:
  - múltiples opciones de huecos alternativos
  - incluir día, hora inicio/fin y duración
  - excluir sábado y domingo
  - proponer desde las 10:00 en adelante
  - ordenar opciones cronológicamente
- Se implementó aceptación por número de opción ("elijo la opción 1") para convertirla en reserva real usando el bloque citado con alternativas.

## 2026-05-21
- Se creó interfaz web con FastAPI + uvicorn (`web_server.py`):
  - Endpoint POST `/api/peticion` que ejecuta `SupervisorFlow` y devuelve categoría, resultado y duración.
  - Frontend HTML integrado con diseño dark, campo de texto, chips de ejemplo y historial de respuestas.
  - Badges de color por categoría (agenda, comunicación, documentos, ambos).
  - Arranca con `uv run python web_server.py` en http://localhost:8000 (puerto configurable con `--port`).
- Se integró el monitor de Gmail dentro del servidor web:
  - Hilo daemon que ejecuta el polling de Gmail en background.
  - Endpoints `/api/gmail/status`, `/api/gmail/start`, `/api/gmail/stop` para controlar el monitor desde la UI.
  - Panel en la interfaz con indicador de estado (dot verde/rojo), contador de emails y botón Iniciar/Detener.
  - Logs expandibles del monitor en tiempo real.
  - Arranque automático por defecto; desactivable con `--no-gmail`.
- Se añadió panel de notificaciones de actividad del monitor:
  - Tarjetas para confirmaciones de reunión (verde), conflictos de agenda (amarillo), emails procesados (azul).
  - Se reubicó como widget flotante fijo arriba a la derecha con scroll interno para no desplazar el formulario.
- Se integró Google Calendar en las peticiones web (`/api/peticion`):
  - Al solicitar una cita desde la UI, se comprueba disponibilidad en Calendar.
  - Si hay hueco, se crea el evento automáticamente y se muestra confirmación con fecha/hora.
  - Si hay conflicto, se muestran hasta 3 alternativas disponibles en la tarjeta de resultado.
  - Se genera un borrador de email listo para copiar (convocatoria o propuesta de alternativas).
  - Las confirmaciones y conflictos se publican como notificaciones en el panel de actividad.
- Se corrigió error de encoding UTF-8 en el HTML por surrogate pairs en emojis JavaScript.
- Se corrigió clasificación errónea de emails de documentos como agenda:
  - El fallback determinista ahora aísla el contenido semántico real del email, ignorando las instrucciones del prompt que contaminaban las regex con palabras como "reunión" o "mañana".
  - Se amplió la detección de intención documental con verbos de búsqueda (buscar, búscalos, localizar, encuentra).
