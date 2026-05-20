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
