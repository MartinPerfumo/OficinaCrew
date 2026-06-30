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

## 2026-06-30

### Implementación RF8: Q&A con cita de fragmentos documentales
- Nueva herramienta `buscar_respuesta_en_documento` en `documentos_tools.py`:
  - Divide el documento por cabeceras `#` en secciones y puntúa por solapamiento de tokens con la pregunta.
  - Devuelve los 3 fragmentos más relevantes con su sección de origen para citar.
  - Si no hay coincidencias, responde explícitamente "no encontrado".
- Actualizado `tasks.yaml` del crew de documentos con formato de cita: `> "fragmento" — archivo, sección "nombre"`.
- Añadido `buscar_respuesta_en_documento` a la lista de tools del agente de documentos.
- `max_iter` del agente de documentos aumentado a 15.
- RF8 marcado como ✅ COMPLETADO en `requisitos.md`.

### Corrección: selección de documento erróneo (teletrabajo vs. vacaciones)
- Reducidos resultados de `buscar_documentos` de 5 a 2 para evitar que el agente cargue el documento secundario.
- Añadida instrucción explícita en `tasks.yaml` paso 1: incluir la palabra clave del tema en la búsqueda.

### Corrección: routing de preguntas sobre política interna
- Añadida función `has_policy_question_intent` en `main.py` con stems (`teletraba`, `vacacion`, `auditoria`…).
- Las preguntas factuales sobre normativa se fuerzan a categoría `documentos` aunque no mencionen la palabra "documento".

### Corrección: detección de formas verbales en búsqueda documental
- Función `_token_matches` con prefijo (mínimo 5 chars o longitud−2) en `_find_best_document_candidate`.
- "teletrabajar" → empareja con "teletrabajo"; resuelve el "no encontré documento" para formas verbales.

### Pestaña "Tareas Pendientes"
- Nueva pestaña "Tareas" en la UI con badge naranja de contador.
- Estado `tareas` + `tareas_contador` en `gmail_monitor_state`; constante `MAX_TAREAS = 100`.
- Función `_store_correo` ampliada: extrae acciones del análisis RF3 y las inserta como tareas.
- Función `_ordenar_tareas`: las tareas con fecha límite aparecen primero (orden cronológico); las sin fecha van al final.
- Nuevos endpoints REST:
  - `GET /api/tareas` – lista de tareas (pendientes y completadas).
  - `POST /api/tareas/{id}/completar` – marca una tarea como completada.
  - `POST /api/tareas/{id}/reabrir` – reactiva una tarea completada.
  - `DELETE /api/tareas/{id}` – elimina una tarea.
- Cada tarea muestra: descripción, prioridad, urgencia (badge), fecha límite con alerta de vencimiento, correo de origen.

### Limpieza de código
- Eliminados imports no utilizados: `json`, `JSONResponse`, `from pydantic import BaseModel`, comentario de ContentCrew.

### Corrección: UnicodeEncodeError 500 en `GET /`
- El emoji 📧 en la plantilla HTML estaba almacenado como par surrogate UTF-16 (`\ud83d\udce7`).
- Reemplazado por entidad HTML `&#x1F4E7;` en la línea JS de `renderTarea`.

### Corrección: fechas límite en tareas
- Prompt de análisis RF1/RF3 actualizado: `fecha_limite` se pide siempre en formato absoluto `DD/MM/YYYY HH:MM`.
- Se inyecta la fecha de hoy en el prompt para que el LLM resuelva fechas relativas ("mañana", "el viernes").
- Si no se menciona hora en el correo, se usa **17:30** por defecto.

### Corrección: clasificación de urgencia sobreestimada
- Se eliminó la condición `prioridad == "alta"` como criterio de urgencia en tareas.
- Prompt actualizado: urgencia solo para palabras explícitas (`urgente`, `ASAP`, `bloqueante`, `crítico`, `emergencia`). Una cita para mañana NO es urgente.

### Corrección: tareas de citas no deben aparecer en el panel Tareas
- Filtro `has_explicit_scheduling_intent` aplicado antes de insertar en `gmail_monitor_state["tareas"]`.

### Corrección: título de evento de calendario incluía verbo de acción
- `create_calendar_event_from_text` limpia el asunto con `re.sub` eliminando verbos iniciales (`Organizar`, `Agendar`, `Programar`, etc.) antes de usarlo como `summary` del evento.
- Ejemplo: "Organizar CITA DEVOPS" → "CITA DEVOPS".

### Fase 1 del ROADMAP — Corrección de fallos funcionales
- **RF3 enrutamiento**: peticiones del tipo "¿qué tareas tengo pendientes?" eran enrutadas al agente de documentos.
  - Nueva función `has_task_query_intent` en `gmail_monitor.py`.
  - Interceptor en `procesar_peticion` (web_server.py): si se detecta intent de tareas, responde directamente desde `gmail_monitor_state` sin invocar `SupervisorFlow`.
  - Nueva función `_handle_task_query` que formatea las tareas pendientes en Markdown.
- **Pipeline correo → tarea (RF3)**:
  - El contador `tareas_contador` solo se incrementa cuando la tarea pasa todos los filtros (antes se incrementaba aunque se descartara).
  - Validación de `fecha_limite`: solo se acepta si coincide exactamente con `DD/MM/YYYY HH:MM`; cualquier texto libre se descarta (`None`).

### Fase 2.1 del ROADMAP — Batería de pruebas con ground truth
- Creado `evaluation/test_cases.json`: 20 casos de prueba estructurados con campo `esperado` para comparación automática.
  - Bloques: RF1_urgencia (4), RF3_tareas (3), clasificacion_peticion (6), RF6 (2), RF7 (2), RF8 (3).
  - Incluye casos de regresión: cita no debe ser urgente, cita no debe generar tarea, "tareas pendientes" no debe ir a documentos.
- Creado `evaluation/benchmark.py`: script de ejecución automática con:
  - Ejecutores por bloque que llaman al sistema real (no mocks).
  - Métricas por bloque: accuracy %, tiempo medio de respuesta.
  - Guardado de resultados en `evaluation/results.json` con timestamp para comparar versiones.
  - Parámetros `--bloque` y `--id` para ejecuciones parciales.
- Creado `ROADMAP.md` con hoja de ruta en 4 fases basada en el feedback del tutor del TFM.

### Análisis de resultados del benchmark (primera ejecución)
- Resultado global: 11/20 (55%) — RF1 y RF3 al 100%; fallos en clasificación y documentos.
- **Falso positivo en `has_task_query_intent`**: "solicitando los días de vacaciones pendientes" activaba el interceptor de tareas por la palabra `pendientes` suelta, clasificando "comunicacion" como "tareas".
  - Fix: reescrita la función con patrones directos (`mis tareas`, `tareas pendientes`, `qué tengo pendiente`…) y exclusión explícita de contextos de redacción/documentos/normativa.
- **`ambos` degradado a `agenda`** (TC-CLAS-04): "envíale un correo de confirmación" no activaba `asks_to_write` porque el verbo `envíale` no estaba en el patrón.
  - Fix: añadidos `envíale`, `envíaselo`, `mándale` al regex de `asks_to_write` en `main.py`.
- **7/9 fallos por rate limit de Groq** (llama-3.1-8b-instant, 6000 TPM): el benchmark ejecutaba casos demasiado seguidos.
  - Fix: añadida pausa de 5 segundos entre casos que invocan LLM en `benchmark.py`.
- Creado `evaluation/validate_intent.py` como script auxiliar de regresión para `has_task_query_intent` (8 casos, todos OK tras el fix).

