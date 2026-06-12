# 3.1 Requisitos funcionales del Sistema

## Gestión de Correo Electrónico

### RF1: Clasificación de emails por urgencia

Los emails recibidos deben ser catalogados en tres categorías: urgente, no urgente y trivial. Para ello, el análisis se basará en múltiples elementos del mensaje, tales como su contenido, el remitente y las palabras clave que puedan indicar la
prioridad del asunto. Adicionalmente, el sistema deberá proporcionar una justificación breve que explique el motivo de cada clasificación asignada.

### RF2: Resumen de contenido de emails
Este requisito contempla la capacidad de generar resúmenes concisos de entre
dos y cuatro frases por cada email, conservando siempre la información más
relevante: quién envía el mensaje, qué se comunica, cuándo sucede, etc.
Adicionalmente, el sistema deberá identificar y resumir hilos de conversación relacionados. (En valoración por si es una complejidad excesiva para el alcance
inicial del proyecto.)

### RF3: Extracción de acciones pendientes
Las acciones solicitadas en los emails, como, por ejemplo, las tareas a realizar,
solicitudes concretas, fechas límite, etc. deberán ser identificadas por el sistema. Todas estas acciones se presentarán en un formato estructurado, claro y comprensible para un usuario promedio

## Gestión de Calendario
Debe existir la posibilidad de poder consultar la disponibilidad del usuario en su
calendario, identificando que lapsos de tiempo, de una duración mínima de una hora,
tiene disponible. Para ello, tendrá en cuenta todos los eventos ya programados con
el fin de evitar cualquier conflicto de agenda.

### RF4: Sugerencia de horarios para reuniones
A partir de la disponibilidad del calendario, el sistema debe proponer varias
opciones de horario para reuniones. Estas sugerencias considerarán las preferencias
horarias del usuario, para evitar, por ejemplo, las primeras o últimas horas del día, o
por determinados días de la semana como los lunes por la mañana o los viernes por
la tarde. Cada propuesta incluirá el día, la hora de inicio y la duración estimada
de la reunión. (En evaluación)

### RF5: Resumen de agenda ✅ COMPLETADO
Se debe generar un resumen de los eventos programados dentro de un rango de
fechas determinado. Este resumen incluirá también la identificación de posibles
conflictos en la agenda que requieran atención por parte del usuario.

## Gestión de Documentos
### RF6: Búsqueda de documentos
Este requisito, valora que el sistema debe permitir buscar documentos mediante
palabras clave, títulos o términos presentes en su contenido. Los resultados se
presentarán ordenados por relevancia (En valoración, quizás es complicado definir
que es relevante y que no). Junto a cada resultado se mostrarán datos básicos del
documento, como su título, fecha de modificación, autor, etc.
### RF7: Resumen de documentos
El sistema debe generar resúmenes de documentos con una extensión de entre
150 y 300 palabras, identificando las secciones o temas principales tratados.
Asimismo, se extraerá la información clave del documento, incluyendo su propósito,
los hallazgos principales y las conclusiones. (En revisión)
### RF8: Extracción de información específica ✅ COMPLETADO
Este requisito, contempla la capacidad del sistema para responder preguntas
concretas sobre el contenido de un documento. El sistema citaría las secciones
relevantes del documento de origen que respaldan cada respuesta e indicaría de
forma explícita cuando la información solicitada no se encuentra en el documento.