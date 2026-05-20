# Sistema Multi-Agente con Supervisor (CrewAI Flows)

Sistema desarrollado como parte del TFM. Implementa un **supervisor** basado en CrewAI Flows que clasifica peticiones de usuario y las enruta únicamente al agente especializado necesario, evitando ejecutar agentes irrelevantes.

## Arquitectura

```
Usuario → Supervisor (Flow) → clasificación LLM
                                    │
                    ┌───────────────┼───────────────┐
                    ▼               ▼               ▼
              Agenda Crew   Comunicación Crew    Ambos
              (reuniones,   (emails, mensajes,
               horarios)     comunicados)
```

Solo se activan los agentes necesarios para cada petición.

## Requisitos

- Python 3.10 – 3.13
- Una API key de **Groq** (gratuita): [console.groq.com](https://console.groq.com)

## Instalación

### 1. Clonar el repositorio

```bash
git clone https://github.com/MartinPerfumo/Ejemplo1.git
cd ejemplo1
```

### 2. Instalar dependencias

Primero instala la CLI de CrewAI (incluye `uv`):

```bash
pip install crewai
```
O si lo prefieres

```bash
python -m pip install crewai
```

Luego instala las dependencias del proyecto:

```bash
crewai install
```

### 3. Configurar la API key

Copia el fichero de ejemplo y añade tu API key de Groq (Es la que he usado yo):

```bash
# Linux / Mac
cp .env.example .env

# Windows
copy .env.example .env
```

Edita `.env` y sustituye `tu_api_key_aqui` por tu clave real de [console.groq.com](https://console.groq.com).

## Uso

```bash
crewai run
```
Cuando pregunte "¿Qué necesitas?", puedes escribir lo que desees. Algunos ejemplos son

```bash
"Organiza una reunión con el equipo de marketing para el viernes"
```
```bash
"Redacta un email al cliente de Telefonica informando del retraso en la entrega"
```
```bash
"Convoca una reunión con el equipo de DevOps el lunes a las 10 y crea el email de invitación"
```
También puedes ejecutar la petición directamente con: (Susituye tu petición por el campo PETICION)
```bash
uv run Ejemplo1 PETICIÓN
```

## Benchmarks

El proyecto incluye una suite de benchmarks para evaluar la precisión del clasificador y la latencia de los crews.

### Archivos

| Archivo | Descripción |
|---|---|
| `benchmark.py` | Script principal de benchmarks |
| `benchmark_cases.py` | 11 casos de prueba (agenda, comunicacion, documentos, ambos) |
| `benchmark_results.json` | Resultados de la última ejecución (generado automáticamente) |

### Ejecución

```bash
# Modo rápido: solo mide la clasificación del supervisor (≈2–5 s por caso)
uv run python benchmark.py

# Modo completo: clasificación + ejecución real de los crews
uv run python benchmark.py --full

# Ejecutar solo el caso nº 3 en modo completo
uv run python benchmark.py --full --caso 3
```

### Métricas recogidas

**Siempre (modo rápido y completo):**
- Latencia de clasificación (ms)
- Categoría predicha vs esperada → precisión global
- P50 / P95 de latencias de clasificación

**Solo en `--full`:**
- Latencia de ejecución del crew (ms) con P50 / P95
- Longitud de la salida (nº de palabras)
- Checks de contenido: palabras clave que deben aparecer en la respuesta

### Criterio de éxito

El script devuelve **exit code 0** si la precisión de clasificación es ≥ 80 % y **exit code 1** en caso contrario, lo que permite integrarlo en pipelines CI.

## Gmail Monitor (Experimental)

**Monitor automático de bandeja de entrada con procesamiento en tiempo real**

Escucha tu bandeja de Gmail y procesa automáticamente cada email que llega, generando resumen y respuesta automática usando SupervisorFlow.

### Configuración

#### 1. Crear credenciales de Google Cloud

1. Ve a [Google Cloud Console](https://console.cloud.google.com)
2. Crea un **proyecto nuevo**
3. Habilita la **Gmail API** y la **Google Calendar API**
4. Crea credenciales de **Aplicación de escritorio (OAuth 2.0)**
5. Descarga el archivo JSON (credentials.json)
6. Copia a `~/.gmail_credentials.json` (carpeta home)

```bash
# Linux / Mac
cp ~/Downloads/credentials.json ~/.gmail_credentials.json

# Windows
copy %USERPROFILE%\Downloads\credentials.json %USERPROFILE%\.gmail_credentials.json
```

#### 2. Autorizar la aplicación

```bash
uv run python setup_gmail.py
```

Se abrirá un navegador para que autorices. El token se guardará en `~/.gmail_token.json`.

Si ya tenías token previo, vuelve a ejecutar `setup_gmail.py` para conceder permisos de Calendar y de envío de correo.

### Uso

```bash
# Escuchar en background (verificar cada 30 segundos)
uv run python gmail_monitor.py

# Verificar cada 60 segundos
uv run python gmail_monitor.py --intervalo 60

# Test: procesar 2 últimos emails y salir
uv run python gmail_monitor.py --test

# Test: procesar 5 últimos emails
uv run python gmail_monitor.py --test --test-count 5
```

### Cómo funciona

1. **Monitor**: Verifica la bandeja de entrada periódicamente
2. **Detección**: Identifica emails sin leer
3. **Procesamiento**: Dispara SupervisorFlow con el contenido del email
4. **Calendar (agenda/ambos)**: Si se detecta reunión, crea evento en Google Calendar automáticamente
5. **Conflictos**: Si hay solape, busca un hueco alternativo y puede responder al remitente proponiéndolo
6. **Resultado**: El LLM genera un resumen y respuesta automática
7. **Marcado**: Marca como leído y añade etiqueta "CrewAI-Procesado"

### State

El monitor mantiene un archivo `.gmail_monitor_state.json` para evitar procesar el mismo email dos veces.

## Estructura del proyecto

```
ejemplo1/
├── .env.example                # Plantilla de configuración
├── pyproject.toml              # Dependencias y metadatos
├── benchmark.py                # Suite de benchmarks (clasificación + crews)
├── benchmark_cases.py          # Casos de prueba del benchmark
├── gmail_monitor.py            # Monitor de Gmail en tiempo real
├── setup_gmail.py              # Setup de autenticación OAuth2 (Google)
└── src/ejemplo1/
    ├── main.py                 # Flow supervisor (clasificación + routing)
    └── crews/
        ├── agenda_crew/        # Agente especializado en agenda
        │   ├── agenda_crew.py
        │   └── config/
        │       ├── agents.yaml
        │       └── tasks.yaml
        ├── comunicacion_crew/  # Agente especializado en comunicación
        │   ├── comunicacion_crew.py
        │   └── config/
        │       ├── agents.yaml
        │       └── tasks.yaml
        └── documentos_crew/    # Agente especializado en documentos
            ├── documentos_crew.py
            └── config/
                ├── agents.yaml
                └── tasks.yaml
```
