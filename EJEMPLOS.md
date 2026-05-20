# Ejemplos de uso — Sistema Multi-Agente con Supervisor

Este documento recoge casos de uso representativos del sistema, organizados por tipo de tarea. En cada caso se muestra la petición del usuario, la clasificación del supervisor y la respuesta del agente especializado.

---

## Índice

1. [Tareas de Agenda](#1-tareas-de-agenda)
2. [Tareas de Comunicación](#2-tareas-de-comunicación)
3. [Tareas de Documentos](#3-tareas-de-documentos)
4. [Tareas Mixtas (Agenda + Comunicación)](#4-tareas-mixtas-agenda--comunicación)

---

## 1. Tareas de Agenda

### Ejemplo 1.1 — Reunión sencilla

**Comando:**
```bash
crewai run "Organiza una reunión con el equipo de marketing para el viernes a las 10h"
```

**Clasificación del supervisor:**
```json
{
  "categoria": "agenda",
  "resumen": "Reunión con equipo de marketing el viernes a las 10h",
  "texto_agenda": "Organiza una reunión con el equipo de marketing para el viernes a las 10h",
  "texto_comunicacion": "",
  "texto_documentos": ""
}
```

**Respuesta del Agente de Agenda:**
```
## Reunión — Equipo de Marketing

- **Fecha:** Viernes
- **Hora:** 10:00h
- **Participantes:** Equipo de Marketing
```

**Evaluación:** ✅ Clasificación correcta · ✅ Enrutado solo al agente de agenda · ✅ Respuesta concisa sin inventar datos

---

### Ejemplo 1.2 — Cita con detalles

**Comando:**
```bash
crewai run "Agenda una cita con el dentista el martes 20 de mayo a las 17:30"
```

**Clasificación del supervisor:**
```json
{
  "categoria": "agenda",
  "resumen": "Cita con dentista el martes 20 de mayo a las 17:30",
  "texto_agenda": "Agenda una cita con el dentista el martes 20 de mayo a las 17:30",
  "texto_comunicacion": "",
  "texto_documentos": ""
}
```

**Respuesta del Agente de Agenda:**
```
## Cita — Dentista

- **Fecha:** Martes, 20 de mayo
- **Hora:** 17:30h
```

**Evaluación:** ✅ Clasificación correcta · ✅ No añade información no solicitada

---

### Ejemplo 1.3 — Planificación semanal

**Comando:**
```bash
crewai run "Planifica la semana del 19 al 23 de mayo: lunes revisión de presupuesto 9h, miércoles presentación a dirección 11h, viernes retrospectiva del sprint 16h"
```

**Clasificación del supervisor:**
```json
{
  "categoria": "agenda",
  "resumen": "Planificación semanal del 19 al 23 de mayo con tres eventos",
  "texto_agenda": "Lunes 19: revisión de presupuesto 9h. Miércoles 21: presentación a dirección 11h. Viernes 23: retrospectiva del sprint 16h",
  "texto_comunicacion": "",
  "texto_documentos": ""
}
```

**Respuesta del Agente de Agenda:**
```
## Agenda Semanal — 19 al 23 de mayo

| Día | Hora | Evento |
|-----|------|--------|
| Lunes 19 | 09:00h | Revisión de presupuesto |
| Miércoles 21 | 11:00h | Presentación a dirección |
| Viernes 23 | 16:00h | Retrospectiva del sprint |
```

**Evaluación:** ✅ Clasifica correctamente una petición con múltiples eventos · ✅ Formato de tabla claro

---

## 2. Tareas de Comunicación

### Ejemplo 2.1 — Email de retraso

**Comando:**
```bash
crewai run "Redacta un email al cliente informando del retraso en la entrega del proyecto, que se retrasa dos semanas por problemas técnicos"
```

**Clasificación del supervisor:**
```json
{
  "categoria": "comunicacion",
  "resumen": "Email al cliente informando retraso de dos semanas por problemas técnicos",
  "texto_agenda": "",
  "texto_comunicacion": "Email al cliente informando del retraso en la entrega del proyecto, que se retrasa dos semanas por problemas técnicos",
  "texto_documentos": ""
}
```

**Respuesta del Agente de Comunicación:**
```
Asunto: Actualización sobre el estado del proyecto

Estimado/a cliente,

Me pongo en contacto para informarle de que la entrega del proyecto 
se verá retrasada aproximadamente dos semanas respecto a la fecha 
acordada, debido a problemas técnicos que han surgido durante 
el desarrollo.

Lamentamos los inconvenientes que esto pueda causarle. Nuestro equipo 
está trabajando para resolver la situación lo antes posible y mantenerle 
informado de cualquier novedad.

Quedamos a su disposición para cualquier consulta.

Atentamente,
[Nombre]
```

**Evaluación:** ✅ Tono profesional · ✅ Incluye disculpa y explicación · ✅ No inventa nombre del cliente

---

### Ejemplo 2.2 — Comunicado interno

**Comando:**
```bash
crewai run "Escribe un comunicado interno para anunciar que la oficina estará cerrada el próximo lunes por obras"
```

**Clasificación del supervisor:**
```json
{
  "categoria": "comunicacion",
  "resumen": "Comunicado interno sobre cierre de oficina el lunes por obras",
  "texto_agenda": "",
  "texto_comunicacion": "Comunicado interno: la oficina estará cerrada el próximo lunes por obras",
  "texto_documentos": ""
}
```

**Respuesta del Agente de Comunicación:**
```
**COMUNICADO INTERNO**

Estimados compañeros/as,

Os informamos de que la oficina permanecerá cerrada el próximo lunes 
debido a la realización de obras en las instalaciones.

Os pedimos que, en la medida de lo posible, organicéis vuestro trabajo 
en modo teletrabajo ese día.

Gracias por vuestra comprensión.

Un saludo,
[Dirección / RRHH]
```

**Evaluación:** ✅ Formato de comunicado interno correcto · ✅ Sugiere teletrabajo de forma natural

---

### Ejemplo 2.3 — Mensaje de seguimiento

**Comando:**
```bash
crewai run "Escribe un mensaje de seguimiento para un candidato entrevistado la semana pasada al que vamos a contratar"
```

**Clasificación del supervisor:**
```json
{
  "categoria": "comunicacion",
  "resumen": "Mensaje de oferta de empleo a candidato entrevistado la semana pasada",
  "texto_agenda": "",
  "texto_comunicacion": "Mensaje de seguimiento para candidato entrevistado la semana pasada, comunicándole que ha sido seleccionado",
  "texto_documentos": ""
}
```

**Respuesta del Agente de Comunicación:**
```
Asunto: Resultado del proceso de selección

Estimado/a [Nombre],

Tras la entrevista mantenida la semana pasada, nos complace comunicarle 
que ha sido seleccionado/a para el puesto.

En los próximos días nos pondremos en contacto con usted para 
concretar los detalles de incorporación.

Enhorabuena y bienvenido/a al equipo.

Un saludo,
[Nombre] — [Empresa]
```

**Evaluación:** ✅ Tono apropiado para una oferta de empleo · ✅ Deja espacio para personalizar

---

## 3. Tareas de Documentos

### Ejemplo 3.1 — Búsqueda por palabras clave

**Comando:**
```bash
crewai run "Busca documentos relacionados con vacaciones"
```

**Clasificación del supervisor:**
```json
{
  "categoria": "documentos",
  "resumen": "Búsqueda de documentos sobre vacaciones",
  "texto_agenda": "",
  "texto_comunicacion": "",
  "texto_documentos": "Busca documentos relacionados con vacaciones"
}
```

**Tool invocada:** `buscar_documentos("vacaciones")`

**Respuesta del Agente de Documentos:**
```
## Búsqueda: "vacaciones"

**Documentos encontrados (ordenados por relevancia):**

- **politica_vacaciones.md** — 8 coincidencias
  Documento principal sobre la política de vacaciones de la empresa.

No se encontraron otros documentos con ese término.
```

**Evaluación:** ✅ Invoca la tool correcta · ✅ Ordena por relevancia · ✅ Describe brevemente cada resultado

---

### Ejemplo 3.2 — Resumen de documento

**Comando:**
```bash
crewai run "Resume el documento politica_vacaciones.md"
```

**Clasificación del supervisor:**
```json
{
  "categoria": "documentos",
  "resumen": "Resumen del documento de política de vacaciones",
  "texto_agenda": "",
  "texto_comunicacion": "",
  "texto_documentos": "Resume el documento politica_vacaciones.md"
}
```

**Tool invocada:** `leer_documento("politica_vacaciones.md")`

**Respuesta del Agente de Documentos:**
```
## Resumen — politica_vacaciones.md

**Días disponibles:** Cada empleado dispone de 23 días laborables anuales, 
no acumulables al siguiente año salvo autorización expresa de RRHH.

**Solicitud:** Mínimo 15 días de antelación (30 días en julio, agosto y diciembre) 
a través del portal interno de RRHH.

**Periodos bloqueados:** Cierre fiscal (15-31 enero), auditoría anual 
(primera semana de junio) y campaña de navidad (20-31 diciembre).

**Aprobación:** La decide el responsable directo. En conflictos entre 
compañeros, tiene prioridad quien solicitó primero.

**Bajas médicas:** No computan como vacaciones; el saldo se preserva íntegro.
```

**Evaluación:** ✅ Sintetiza en lugar de copiar · ✅ Estructura clara · ✅ Cubre todos los puntos del documento

---

### Ejemplo 3.3 — Extracción de sección

**Comando:**
```bash
crewai run "Extrae la sección de periodos bloqueados de politica_teletrabajo.md"
```

**Clasificación del supervisor:**
```json
{
  "categoria": "documentos",
  "resumen": "Extraer sección de periodos bloqueados de política de teletrabajo",
  "texto_agenda": "",
  "texto_comunicacion": "",
  "texto_documentos": "Extrae la sección periodos bloqueados de politica_teletrabajo.md"
}
```

**Tool invocada:** `extraer_seccion("politica_teletrabajo.md", "Periodos bloqueados")`

**Respuesta del Agente de Documentos:**
```
## Sección extraída — politica_teletrabajo.md

### Periodos bloqueados

No se podrá teletrabajar durante:
- Reuniones de equipo obligatorias (convocadas por la dirección)
- Periodo de incorporación de nuevos empleados (primeras 4 semanas)
- Auditoría anual (primera semana de junio)
```

**Evaluación:** ✅ Extrae exactamente la sección solicitada · ✅ No incluye contenido de otras secciones

---

### Ejemplo 3.4 — Comparación de documentos

**Comando:**
```bash
crewai run "Compara los documentos politica_teletrabajo.md y politica_vacaciones.md"
```

**Clasificación del supervisor:**
```json
{
  "categoria": "documentos",
  "resumen": "Comparación entre política de teletrabajo y política de vacaciones",
  "texto_agenda": "",
  "texto_comunicacion": "",
  "texto_documentos": "Compara politica_teletrabajo.md y politica_vacaciones.md"
}
```

**Tool invocada:** `comparar_documentos("politica_teletrabajo.md", "politica_vacaciones.md")`

**Respuesta del Agente de Documentos:**
```
## Comparación — politica_teletrabajo.md vs politica_vacaciones.md

### Similitudes
- Ambas incluyen **periodos bloqueados** (auditoría anual en junio aparece en las dos)
- Ambas requieren **acuerdo previo** con el responsable directo
- Ambas mencionan el tratamiento durante **bajas médicas**

### Diferencias principales

| Aspecto | Teletrabajo | Vacaciones |
|---------|-------------|------------|
| Unidad | Días/semana (hasta 2) | Días/año (23) |
| Antelación | 48 horas | 15-30 días |
| Acumulación | No aplica | No acumulable |
| Equipamiento | Portátil + VPN provisto | No aplica |
```

**Evaluación:** ✅ Identifica similitudes no obvias · ✅ Formato de tabla para diferencias · ✅ Análisis más allá del diff técnico

---

## 4. Tareas Mixtas (Agenda + Comunicación)

### Ejemplo 4.1 — Convocar reunión y enviar invitación

**Comando:**
```bash
crewai run "Convoca una reunión con el equipo de ventas el jueves a las 9h para revisar el cierre del trimestre, y envía el email de invitación"
```

**Clasificación del supervisor:**
```json
{
  "categoria": "ambos",
  "resumen": "Reunión con equipo de ventas el jueves 9h para revisar cierre de trimestre + email de convocatoria",
  "texto_agenda": "Reunión con equipo de ventas el jueves a las 9h para revisar el cierre del trimestre",
  "texto_comunicacion": "Email de convocatoria para reunión del equipo de ventas el jueves a las 9h, motivo: revisión del cierre del trimestre",
  "texto_documentos": ""
}
```

**Respuesta del Agente de Agenda:**
```
## Reunión — Equipo de Ventas

- **Fecha:** Jueves
- **Hora:** 09:00h
- **Participantes:** Equipo de Ventas
- **Motivo:** Revisión del cierre del trimestre
```

**Respuesta del Agente de Comunicación:**
```
Asunto: Convocatoria de reunión — Revisión cierre de trimestre

Estimado equipo,

Os convoco a una reunión el próximo jueves a las 9:00h para revisar 
el cierre del trimestre.

Por favor, confirmad vuestra asistencia.

Un saludo,
[Nombre]
```

**Evaluación:** ✅ Detecta la tarea mixta correctamente · ✅ Ambos agentes se ejecutan · ✅ Cada uno recibe solo su parte relevante

---

### Ejemplo 4.2 — Posponer reunión y notificar

**Comando:**
```bash
crewai run "Pospón la reunión de dirección del lunes al miércoles a las 12h y avisa a todos los participantes por email"
```

**Clasificación del supervisor:**
```json
{
  "categoria": "ambos",
  "resumen": "Cambio de reunión de dirección de lunes a miércoles 12h + email de notificación",
  "texto_agenda": "Posponer reunión de dirección del lunes al miércoles a las 12h",
  "texto_comunicacion": "Email notificando cambio de reunión de dirección: se pospone del lunes al miércoles a las 12h",
  "texto_documentos": ""
}
```

**Respuesta del Agente de Agenda:**
```
## Reunión de Dirección — Actualización

- ~~Lunes~~ → **Miércoles, 12:00h** ✓
```

**Respuesta del Agente de Comunicación:**
```
Asunto: Cambio de fecha — Reunión de Dirección

Estimados/as,

Os informamos de que la reunión de dirección prevista para el lunes 
queda pospuesta al **miércoles a las 12:00h**.

Por favor, actualicen sus agendas.

Un saludo,
[Nombre]
```

**Evaluación:** ✅ Clasifica correctamente como tarea mixta · ✅ El email refleja exactamente el cambio de la agenda

---

## Resumen de Evaluación

| # | Petición | Clasificación | Enrutamiento | Calidad respuesta |
|---|----------|---------------|--------------|-------------------|
| 1.1 | Reunión sencilla | ✅ agenda | ✅ solo agenda | ✅ concisa |
| 1.2 | Cita con detalles | ✅ agenda | ✅ solo agenda | ✅ fiel a los datos |
| 1.3 | Planificación semanal | ✅ agenda | ✅ solo agenda | ✅ formato tabla |
| 2.1 | Email retraso | ✅ comunicacion | ✅ solo comunicación | ✅ tono profesional |
| 2.2 | Comunicado interno | ✅ comunicacion | ✅ solo comunicación | ✅ formato correcto |
| 2.3 | Seguimiento candidato | ✅ comunicacion | ✅ solo comunicación | ✅ tono adecuado |
| 3.1 | Búsqueda por keywords | ✅ documentos | ✅ solo documentos | ✅ ordenado por relevancia |
| 3.2 | Resumen documento | ✅ documentos | ✅ solo documentos | ✅ sintetiza sin copiar |
| 3.3 | Extracción de sección | ✅ documentos | ✅ solo documentos | ✅ preciso |
| 3.4 | Comparación docs | ✅ documentos | ✅ solo documentos | ✅ análisis profundo |
| 4.1 | Reunión + email | ✅ ambos | ✅ ambos agentes | ✅ coordinados |
| 4.2 | Posponer + notificar | ✅ ambos | ✅ ambos agentes | ✅ coherentes entre sí |

**Tasa de clasificación correcta: 12/12 (100%)**  
**Tasa de enrutamiento correcto: 12/12 (100%)**

> Nota: estos ejemplos han sido ejecutados en condiciones normales de uso con el modelo `groq/llama-3.3-70b-versatile` y la API key del tier gratuito de Groq.
