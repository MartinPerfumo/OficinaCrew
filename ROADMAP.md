# Hoja de ruta TFM — OficinaCrew

Basada en el feedback del tutor. Orden de prioridad: primero corregir fallos, luego evaluar, luego documentar.

---

## Fase 1 — Corrección de fallos funcionales

Objetivo: que el sistema sea fiable antes de medir.

- [ ] **RF3 — Supervisor enruta mal "tareas pendientes"** al agente de documentos en lugar de recuperar acciones pendientes. Revisar clasificador y añadir categoría explícita `tareas`.
- [ ] **Revisar casos de alucinación** con el modelo de fallback (`llama-3.1-8b-instant`): documentar en qué casos degrada la calidad y si merece un aviso en la UI.
- [ ] **Validar pipeline completo de correo → tarea** (RF3): que un correo con acción pendiente genere exactamente la tarea correcta en el panel Tareas, sin duplicados ni entradas vacías.
- [ ] **Correos ya procesados**: verificar que no se reprocesen en cada ciclo del monitor (deduplicación por `message_id`).

---

## Fase 2 — Batería de pruebas con ground truth

Objetivo: poder decir "el sistema acierta X de cada 10 casos" con datos reales.

### 2.1 Diseño de los casos de prueba

Crear el fichero `evaluation/test_cases.json` con casos estructurados:

```
{
  "id": "TC-01",
  "tipo": "clasificacion",
  "input": { "asunto": "...", "cuerpo": "..." },
  "esperado": { "categoria": "agenda", "urgencia": "no urgente" }
}
```

Cubrir al menos 3 casos por bloque:

| Bloque | Qué se evalúa |
|--------|---------------|
| Clasificación de petición | categoría correcta (agenda/comunicacion/documentos/ambos) |
| Selección de agente | agente activado coincide con el esperado |
| Urgencia de correo (RF1) | urgente / no urgente / trivial |
| Extracción de tareas (RF3) | acción extraída, fecha límite, prioridad |
| Creación de evento (RF4) | evento creado con fecha/hora correcta |
| Detección de solapamiento | conflicto detectado y alternativas propuestas |
| Resumen de agenda (RF5) | eventos incluidos, rango correcto |
| Búsqueda de documento (RF6) | documento correcto seleccionado |
| Resumen documental (RF7) | resumen relevante generado |
| Q&A con cita (RF8) | fragmento correcto, sección citada |

### 2.2 Ejecución y registro de resultados

- [ ] Ampliar `benchmark.py` para leer `evaluation/test_cases.json` y ejecutar cada caso automáticamente.
- [ ] Registrar: resultado obtenido, resultado esperado, `pass/fail`, tiempo de respuesta, tokens usados.
- [ ] Calcular métricas por bloque:
  - **Accuracy** = aciertos / total
  - **Tasa de error** = fallos / total
  - **Tiempo medio de respuesta** (ms)
  - **Coste estimado** (tokens × precio por token)
- [ ] Guardar resultados en `evaluation/results.json` con timestamp para poder comparar versiones.

---

## Fase 3 — Documentación del proceso de desarrollo

Objetivo: justificar las decisiones de diseño e ingeniería para el TFM.

### 3.1 Decisiones de diseño a documentar

- [ ] Por qué tres agentes separados (agenda / comunicacion / documentos) y no más o menos.
- [ ] Cómo funciona el supervisor (`SupervisorFlow`): clasificación por LLM + regex de seguridad.
- [ ] Qué información conserva el sistema entre peticiones (estado en memoria, sin persistencia a disco).
- [ ] Cómo se evita reactivar agentes innecesarios (cortocircuito por categoría).
- [ ] Cómo se gestionan correos ya procesados (deduplicación por `message_id`).
- [ ] Por qué se usa fallback a modelo más barato: motivación de coste vs. calidad, con ejemplos de degradación observada.
- [ ] Límite de contexto: qué ocurre cuando se alcanza y cómo se maneja (`respect_context_window`).

### 3.2 Documentación de fallos conocidos

Añadir sección en la memoria del TFM con tabla:

| ID | Fallo observado | Causa identificada | Estado |
|----|-----------------|-------------------|--------|
| F-01 | Petición de tareas → agente documentos | Clasificador no distingue "tareas" de "documentos" | Pendiente |
| F-02 | Urgencia sobreestimada | Prompt incluía proximidad de fecha como criterio | Corregido |
| F-03 | Título de evento incluye verbo de acción | Se usaba asunto raw sin limpiar | Corregido |
| ... | | | |

### 3.3 Bitácora técnica

- [ ] Actualizar `bitacora.md` con cada cambio significativo: fecha, qué se cambió, por qué.

---

## Fase 4 — Mejoras opcionales (si hay tiempo)

Solo abordar si las fases 1–3 están completas.

- [ ] **Persistencia de tareas** a disco (JSON o SQLite) para que sobrevivan reinicios del servidor.
- [ ] **Panel de métricas en la UI**: mostrar accuracy por RF directamente en la interfaz.
- [ ] **Modelo de evaluación automática**: usar un LLM juez para valorar respuestas abiertas (resúmenes, borradores) donde no hay ground truth exacto.
- [ ] **Tests de regresión** en CI: ejecutar `benchmark.py` en cada commit y alertar si el accuracy baja.

---

## Orden recomendado

```
Semana actual   → Fase 1: corregir fallos F-01 y validar pipeline correo→tarea
Semana +1       → Fase 2.1: diseñar y escribir los casos de prueba (mínimo 30 casos)
Semana +2       → Fase 2.2: ejecutar benchmark y calcular métricas
Semana +3/+4    → Fase 3: documentar decisiones, fallos y bitácora para la memoria del TFM
Si sobra tiempo → Fase 4: mejoras opcionales
```
