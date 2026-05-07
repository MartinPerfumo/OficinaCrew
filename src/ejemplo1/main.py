#!/usr/bin/env python

import sys
import json
import warnings

from pathlib import Path

from pydantic import BaseModel

from crewai import LLM
from crewai.flow import Flow, listen, start, router

#<|vq_15391|>from ejemplo1.crews.content_crew.content_crew import ContentCrew

warnings.filterwarnings("ignore", category=SyntaxWarning, module="pysbd") # Ignorar advertencias de sintaxis de pysbd

llm = LLM(model="groq/llama-3.3-70b-versatile")

class SupervisorFlow(Flow):
    
    """
    Flow supervisor que:
    1. Recibe la petición del usuario
    2. Clasifica a qué agente(s) debe delegarla
    3. Enruta solo al crew necesario
    """

    @start()
    def classify_request(self):
        """Clasifica la petición del usuario usando el LLM."""
        peticion = self.state.get("peticion", "")

        prompt = f"""Eres un clasificador de tareas de oficina. 
Analiza la siguiente petición y determina qué tipo de tarea es.

Petición: "{peticion}"

Responde ÚNICAMENTE con un JSON válido con este formato:
{{"categoria": "<categoria>", "resumen": "<breve descripcion sin obviar datos importantes>", "texto_agenda": "<Texto que corresponde al agente de agenda>", "texto_comunicacion": "<Texto que el agente de comunicacion debe usar para redactar la comunicacion>"}}

texto_agenda y texto_comunicacion deben contener SOLO la información relevante para cada agente, sin datos innecesarios ni irrelevantes. Si no hay información relevante para alguno de los agentes, deja su campo vacío.

Las categorías disponibles son:
- "agenda": para reuniones, citas, planificación de horarios
- "comunicacion": para redactar emails, mensajes, comunicados
- "ambos": cuando requiere TANTO agenda COMO comunicación

Responde SOLO con el JSON, sin texto adicional."""
        
        response = llm.call(prompt) # Llamada al LLM para clasificar la petición


        # Parsear JSON de la respuesta
        try:
            # Intentar extraer JSON del texto
            text = response if isinstance(response, str) else str(response) # Asegurarse de que la respuesta es una cadena de texto
            start_idx = text.find("{")
            end_idx = text.rfind("}") + 1
            if start_idx != -1 and end_idx > start_idx: # Si se encuentra un JSON válido en la respuesta, parsearlo
                result = json.loads(text[start_idx:end_idx]) # Extraer el JSON de la respuesta y parsearlo
            else:
                result = {"categoria": "comunicacion", "resumen": peticion, "texto_agenda": "", "texto_comunicacion": ""} # Si no se encuentra un JSON válido, asignar una categoría por defecto (comunicacion) y usar la petición como resumen
        except (json.JSONDecodeError, ValueError): # Si ocurre un error al parsear el JSON, asignar una categoría por defecto (comunicacion) y usar la petición como resumen
            result = {"categoria": "comunicacion", "resumen": peticion, "texto_agenda": "", "texto_comunicacion": ""} # type: ignore
        
        self.state["clasificacion"] = result # Guardar la clasificación en el estado del flow para usarla en pasos posteriores
        print(f"\n{'='*60}")
        print(f"[SUPERVISOR] Clasificación: {result['categoria']}")
        print(f"[SUPERVISOR] Resumen: {result.get('resumen', '')}")
        print(f"[SUPERVISOR] Texto Agenda: {result.get('texto_agenda', '')}")
        print(f"[SUPERVISOR] Texto Comunicación: {result.get('texto_comunicacion', '')}")
        print(f"{'='*60}\n")

        return result["categoria"] # Devolver la categoría para enrutar al crew correspondiente

    @router(classify_request)
    def route(self):
        """Enruta la ejecución al listener correcto."""
        categoria = self.state["clasificacion"]["categoria"]
        if categoria == "agenda":
            return "agenda"
        elif categoria == "comunicacion":
            return "comunicacion"
        return "ambos"


    @listen("agenda")
    def handle_agenda(self):
        """Ejecuta solo el crew de agenda."""
        print("[SUPERVISOR] → Delegando a Agente de Agenda\n")
        from ejemplo1.crews.agenda_crew.agenda_crew import AgendaCrew # Importar el crew de agenda dentro del método para evitar importaciones circulares

        result = AgendaCrew().crew().kickoff(
            inputs={"peticion": self.state["clasificacion"]["texto_agenda"]}
        )
      
        self.state["resultado_agenda"] = result.raw
        return result.raw

    @listen("comunicacion")
    def handle_comunicacion(self):
        """Ejecuta solo el crew de comunicación."""
        print("[SUPERVISOR] → Delegando a Agente de Comunicación\n")
        from ejemplo1.crews.comunicacion_crew.comunicacion_crew import ComunicacionCrew # Importar el crew de comunicación dentro del método para evitar importaciones circulares

        result = ComunicacionCrew().crew().kickoff(
            inputs={"peticion": self.state["clasificacion"]["texto_comunicacion"]} # Usar el texto específico para comunicación generado por el clasificador
        )

        self.state["resultado_comunicacion"] = result.raw
        return result.raw

    @listen("ambos")
    def handle_ambos(self):
        """Ejecuta ambos crews cuando la petición lo requiere."""
        print("[SUPERVISOR] → Delegando a AMBOS agentes\n")
        from ejemplo1.crews.agenda_crew.agenda_crew import AgendaCrew
        from ejemplo1.crews.comunicacion_crew.comunicacion_crew import ComunicacionCrew
        
        result_agenda = AgendaCrew().crew().kickoff(
            inputs={"peticion": self.state["clasificacion"]["texto_agenda"]}
        )
        self.state["resultado_agenda"] = result_agenda.raw

        result_com = ComunicacionCrew().crew().kickoff(
            inputs={"peticion": self.state["clasificacion"]["texto_comunicacion"]}
        )
        self.state["resultado_comunicacion"] = result_com.raw

        return f"{result_agenda.raw}\n\n---\n\n{result_com.raw}"


def run():
    """
    Punto de entrada principal.
    """
    if len(sys.argv) > 1:
        peticion = " ".join(sys.argv[1:]).strip()
    else:
        peticion = input("¿Qué necesitas? → ").strip()
    print(f"\n[USUARIO] {peticion}\n")

    # Create flow and kickoff with trigger payload
    # The @start() methods will automatically receive crewai_trigger_payload parameter
    flow = SupervisorFlow()
    result = flow.kickoff(inputs={"peticion": peticion})

    print(f"\n{'='*60}")
    print("[RESULTADO FINAL]")
    print(f"{'='*60}")
    print(result)

if __name__ == "__main__":
    run()
