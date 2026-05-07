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
# Tarea de agenda
crewai run "Organiza una reunión con el equipo de marketing para el viernes"

# Tarea de comunicación
crewai run "Redacta un email al cliente de Telefonica informando del retraso en la entrega"

# Tarea mixta (activa ambos agentes)
crewai run "Convoca una reunión con el equipo de DevOps y crea el email de invitación"
```

## Estructura del proyecto

```
ejemplo1/
├── .env.example                # Plantilla de configuración
├── pyproject.toml              # Dependencias y metadatos
└── src/ejemplo1/
    ├── main.py                 # Flow supervisor (clasificación + routing)
    └── crews/
        ├── agenda_crew/        # Agente especializado en agenda
        │   ├── agenda_crew.py
        │   └── config/
        │       ├── agents.yaml
        │       └── tasks.yaml
        └── comunicacion_crew/  # Agente especializado en comunicación
            ├── comunicacion_crew.py
            └── config/
                ├── agents.yaml
                └── tasks.yaml
```
