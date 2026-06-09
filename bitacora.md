# Bitácora del Proyecto

## 2026-05-07 (Previo al 19)
- Commit inicial del proyecto: sistema multi-agente con Supervisor Flow en CrewAI.
- Estructura base creada: configuración del proyecto, dependencias y lockfile (`pyproject.toml`, `uv.lock`, `.env.example`, `.gitignore`).
- Documentación inicial añadida (`README.md`, `AGENTS.md`).
- Implementación inicial de arquitectura por crews:
  - Agenda crew (agentes + tareas YAML + código).
  - Comunicación crew (agentes + tareas YAML + código).
- Flujo supervisor inicial en `src/oficinacrew/main.py`.
- Módulo de tools base preparado (`src/oficinacrew/tools/`).

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

## 2026-06-04
- Se reforzó el flujo de solicitudes documentales por email en `gmail_monitor.py`:
  - La respuesta de categoría `documentos` ahora se prepara como **borrador de Gmail** (Drafts) para revisión humana, en lugar de enviarse automáticamente.
  - Se añadió función dedicada para crear borradores de respuesta en hilo (`create_email_reply_draft`).
- Se corrigió la extracción del resultado documental desde el estado del flow:
  - `procesar_email_con_crewai` ahora expone explícitamente `resultado_documentos` además de `resumen`.
  - El loop del monitor prioriza `resultado_documentos` al construir el contenido del borrador.
- Se mejoró la robustez de lectura del cuerpo de email:
  - Si no existe `text/plain`, se usa fallback desde `text/html` limpiando etiquetas.
- Se simplificó el prompt interno del monitor para clasificación de emails:
  - Se eliminaron instrucciones adicionales que contaminaban la semántica.
  - Si el cuerpo viene vacío, se usa el asunto como contenido para evitar perder contexto.
- Se añadieron logs diagnósticos para categoría `documentos` (resultado vacío, remitente no extraído, creación de borrador).
- **Bug detectado y corregido: UnicodeEncodeError en Windows bloqueaba silenciosamente el flujo de documentos.**
  - Síntoma: el flow completaba correctamente (state con `resultado_documentos` poblado), pero el bloque `[DOCUMENTOS]` nunca se ejecutaba y no se creaba el borrador.
  - Causa raíz: el panel de progreso de CrewAI usa emojis (🌊, ✅, 🔄...) que la consola de Windows (encoding `cp1252`/`charmap`) no puede representar. El `UnicodeEncodeError` lanzado por `CrewAIEventsBus` hacía que `flow.kickoff()` propagara la excepción y `procesar_email_con_crewai` devolviera `success: False`, impidiendo llegar al bloque de creación de borrador.
  - Diagnóstico: ejecutando `gmail_monitor.py --test` con `Select-String` se observaron líneas `[CrewAIEventsBus] Sync handler error: 'charmap' codec can't encode character` intercaladas con el output normal, confirmando que el error era de encoding y no de lógica.
  - Solución 1 (`gmail_monitor.py`, inicio): se fuerza UTF-8 en `sys.stdout` y `sys.stderr` al arrancar el script, eliminando el error de raíz.
  - Solución 2 (`procesar_email_con_crewai`): se añadió manejo defensivo: si `kickoff` lanza una excepción pero `flow.state["clasificacion"]` ya está poblado (el flow completó), se extraen `clasificacion` y `resultado_documentos` del state y se devuelve `success: True` igualmente, garantizando que el borrador se cree aunque la consola falle.

## 2026-06-09

### Mejora RF1/RF3: bloque de urgencia prominente en terminal
- Al procesar un email real, la salida de terminal ahora muestra un bloque visual destacado (`████`) con:
  - **RF1** – nivel de urgencia en mayúsculas con color ANSI (rojo=urgente, amarillo=no urgente, verde=trivial) y justificación.
  - **RF3** – lista de acciones pendientes con responsable, fecha límite y prioridad codificada en color.
- Antes la información de urgencia se perdía entre los paneles de CrewAI; ahora aparece siempre inmediatamente después del resultado del flow.

### Pestaña "Correos" en la interfaz web
- La UI pasa de una sola pantalla a un diseño con **dos pestañas**:
  - **Asistente** – interfaz de petición existente, sin cambios.
  - **Correos** – inbox de emails procesados por el monitor de Gmail.
- Nuevo estado `correos` + `correos_sin_leer` en `gmail_monitor_state`.
- Nueva constante `MAX_CORREOS = 50` para circular el inbox.
- Función `_store_correo(email, resultado)` que extrae y almacena para cada email: asunto, remitente, categoría, urgencia, justificación, resumen, posible respuesta y acciones pendientes.
- Nuevos endpoints:
  - `GET /api/gmail/correos` – devuelve la lista de correos y el contador de no leídos.
  - `POST /api/gmail/correos/leidos` – marca todos como leídos y resetea el contador.
- Badge rojo en la pestaña "Correos" con el número de emails sin leer; desaparece al abrir la pestaña.
- Cada email se muestra como una **card** con:
  - Badge de categoría + badge de urgencia con color (rojo/azul/gris).
  - Sección **Resumen** del contenido.
  - Sección **Acciones pendientes** con prioridad y fecha límite (si las hay).
  - Sección **Posible respuesta** colapsable (expandir con clic).
- El panel de correos se refresca automáticamente cada 10 segundos.
