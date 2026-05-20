#!/usr/bin/env python

import os
import sys
import json
import re
import warnings

from pathlib import Path

from pydantic import BaseModel

os.environ.setdefault("CREWAI_DISABLE_TELEMETRY", "true")
os.environ.setdefault("OTEL_SDK_DISABLED", "true")

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
{{"categoria": "<categoria>", "resumen": "<breve descripcion sin obviar datos importantes>", "texto_agenda": "<Texto que corresponde al agente de agenda>", "texto_comunicacion": "<Texto que el agente de comunicacion debe usar para redactar la comunicacion>", "texto_documentos": "<Texto o consulta para el agente de documentos>"}}

Cada campo de texto debe contener SOLO la información relevante para ese agente. Si no hay información relevante para un agente, deja su campo vacío.

Las categorias disponibles son:
- "agenda": para reuniones, citas, planificacion de horarios. USA SOLO ESTA cuando la peticion es organizar un evento SIN pedir que se redacte ningun email ni mensaje.
- "comunicacion": para redactar emails, mensajes, comunicados. USA SOLO ESTA cuando solo se pide redactar texto.
- "documentos": para buscar, leer, extraer secciones o comparar documentos.
- "ambos": SOLO cuando la peticion pide EXPLICITAMENTE tanto crear/modificar una cita/reunion COMO enviar/redactar un email o mensaje. Si hay duda, elige la categoria mas especifica.

Reglas IMPORTANTES anti-ambiguedad:
- Solo clasifica como "agenda" o "ambos" cuando exista INTENCION EXPLICITA de agendar (verbos como: fijar, programar, reprogramar, agendar, convocar, organizar una cita).
- Mencionar una reunion futura como contexto (ej: "tengo reunion manana") NO implica agenda por si solo.
- Si la peticion principal es pedir/buscar/preparar documentos, clasifica como "documentos" aunque mencione una reunion.
- Si la peticion contiene una reunion/cita con dia y/o hora y ADEMAS pide responder/redactar/confirmar por email o mensaje, clasifica como "ambos" SOLO si tambien hay intencion explicita de agendar.
- Frases de cortesia en emails ("quedo a la espera", "gracias", "un saludo") NO implican "comunicacion" por si solas.
- Palabras como "respuesta", "confirmacion" o "confirmar" solo activan "comunicacion" si van unidas a redactar/escribir/enviar un correo o mensaje.
- Solo considera "comunicacion" si la peticion pide redactar, escribir, preparar o enviar un correo/mensaje de forma explicita.
- Si el contenido principal es fijar/confirmar/reprogramar una reunion, prioriza "agenda".

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
                result = {"categoria": "comunicacion", "resumen": peticion, "texto_agenda": "", "texto_comunicacion": "", "texto_documentos": ""} # Si no se encuentra un JSON válido, asignar una categoría por defecto (comunicacion) y usar la petición como resumen
        except (json.JSONDecodeError, ValueError): # Si ocurre un error al parsear el JSON, asignar una categoría por defecto (comunicacion) y usar la petición como resumen
            result = {"categoria": "comunicacion", "resumen": peticion, "texto_agenda": "", "texto_comunicacion": "", "texto_documentos": ""} # type: ignore

        # Fallback determinista con prioridad a intención explícita.
        text_for_rules = peticion.lower()
        has_scheduling_intent = bool(
            re.search(
                r"\b(fijar|fija|fijamos|programar|programa|programamos|reprogramar|reprograma|agendar|agenda|agendamos|convocar|convoca|convocamos|organizar|organiza|organizamos|coordinar|coordina|coordinamos|reservar|reserva|reservamos|reconfirmar|reconfirma|reconfirmamos|crear\s+(cita|evento))\b|\b(organizar|organiza|organizamos|coordinar|coordina|coordinamos|reservar|reserva|reservamos)\s+(una\s+)?(reunion|reunión|cita)\b|\b(dejarla\s+reservada|dejar\s+reservada|quedar\s+reservada|quede\s+reservada|quedar\s+la\s+reservada)\b",
                text_for_rules,
            )
        )
        has_meeting_reference = bool(re.search(r"\b(reunion|reunión|cita)\b", text_for_rules))
        has_datetime_hint = bool(
            re.search(
                r"\b(\d{1,2}[:.]\d{2}|\d{1,2}[/-]\d{1,2}|lunes|martes|miercoles|miércoles|jueves|viernes|sabado|sábado|domingo|manana|mañana|hoy)\b",
                text_for_rules,
            )
        )
        has_document_intent = bool(
            re.search(r"\b(documento|documentos|informe|adjunto|adjuntar|presentar|expediente|contrato)\b", text_for_rules)
        )
        asks_to_write = bool(
            re.search(
                r"\b(redacta|redactar|escribe|escribir|prepara|preparar|redacción|redaccion|enviar|envia|envía|mandar|manda|remitir|remite)\b"
                r".*\b(email|correo|mensaje|comunicado|respuesta)\b|"
                r"\b(email|correo|mensaje|comunicado)\b.*\b(redacta|redactar|escribe|escribir|prepara|preparar|enviar|envia|envía|mandar|manda|remitir|remite)\b",
                text_for_rules,
            )
        )
        has_option_selection_intent = bool(
            re.search(
                r"\b(elijo|elige|escojo|escoge|selecciono|selecciona|me\s+quedo\s+con)\b.*\b(opcion|opción)\b.*\b(\d{1,2})\b|"
                r"\b(opcion|opción)\b\s*(?:n[uú]mero\s*)?(\d{1,2})\b",
                text_for_rules,
            )
        )
        asks_for_reply = bool(
            re.search(
                r"\b(confirma|confirmar|confirmación|confirmacion|respuesta|responder|contestar)\b",
                text_for_rules,
            )
        )

        if has_document_intent and not has_scheduling_intent:
            result["categoria"] = "documentos"
            if not str(result.get("texto_documentos", "")).strip():
                result["texto_documentos"] = peticion
            if "texto_agenda" in result:
                result["texto_agenda"] = ""
            if "texto_comunicacion" in result and not asks_to_write:
                result["texto_comunicacion"] = ""

        elif has_option_selection_intent:
            result["categoria"] = "agenda"
            if not str(result.get("texto_agenda", "")).strip():
                result["texto_agenda"] = peticion
            if "texto_comunicacion" in result:
                result["texto_comunicacion"] = ""

        elif has_scheduling_intent and has_meeting_reference and has_datetime_hint:
            result["categoria"] = "ambos" if asks_to_write else "agenda"
            if not str(result.get("texto_agenda", "")).strip():
                result["texto_agenda"] = peticion
            if asks_to_write and not str(result.get("texto_comunicacion", "")).strip():
                result["texto_comunicacion"] = peticion
            if not asks_to_write and "texto_comunicacion" in result:
                result["texto_comunicacion"] = ""

        # Si la petición solo pide gestionar la cita pero no redactar un correo,
        # mantenemos agenda aunque el texto del LLM haya sugerido comunicación.
        if result.get("categoria") == "ambos" and not asks_to_write and not asks_for_reply:
            result["categoria"] = "agenda"
            if "texto_comunicacion" in result:
                result["texto_comunicacion"] = ""
        
        self.state["clasificacion"] = result # Guardar la clasificación en el estado del flow para usarla en pasos posteriores
        print(f"\n{'='*60}")
        print(f"[SUPERVISOR] Clasificación: {result['categoria']}")
        print(f"[SUPERVISOR] Resumen: {result.get('resumen', '')}")
        print(f"[SUPERVISOR] Texto Agenda: {result.get('texto_agenda', '')}")
        print(f"[SUPERVISOR] Texto Comunicación: {result.get('texto_comunicacion', '')}")
        print(f"[SUPERVISOR] Texto Documentos: {result.get('texto_documentos', '')}")
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
        elif categoria == "documentos":
            return "documentos"
        return "ambos"


    @listen("agenda")
    def handle_agenda(self):
        """Ejecuta solo el crew de agenda."""
        print("[SUPERVISOR] Delegando a Agente de Agenda\n")
        from ejemplo1.crews.agenda_crew.agenda_crew import AgendaCrew # Importar el crew de agenda dentro del método para evitar importaciones circulares

        result = AgendaCrew().crew().kickoff(
            inputs={"peticion": self.state["clasificacion"]["texto_agenda"]}
        )
      
        self.state["resultado_agenda"] = result.raw
        return result.raw

    @listen("comunicacion")
    def handle_comunicacion(self):
        """Ejecuta solo el crew de comunicación."""
        print("[SUPERVISOR] Delegando a Agente de Comunicacion\n")
        from ejemplo1.crews.comunicacion_crew.comunicacion_crew import ComunicacionCrew # Importar el crew de comunicación dentro del método para evitar importaciones circulares

        result = ComunicacionCrew().crew().kickoff(
            inputs={"peticion": self.state["clasificacion"]["texto_comunicacion"]} # Usar el texto específico para comunicación generado por el clasificador
        )

        self.state["resultado_comunicacion"] = result.raw
        return result.raw

    @listen("ambos")
    def handle_ambos(self):
        """Ejecuta ambos crews cuando la petición lo requiere."""
        print("[SUPERVISOR] Delegando a AMBOS agentes\n")
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

    @listen("documentos")
    def handle_documentos(self):
        """Ejecuta solo el crew de documentos."""
        print("[SUPERVISOR] Delegando a Agente de Documentos\n")
        from ejemplo1.crews.documentos_crew.documentos_crew import DocumentosCrew

        result = DocumentosCrew().crew().kickoff(
            inputs={"peticion": self.state["clasificacion"]["texto_documentos"]}
        )

        self.state["resultado_documentos"] = result.raw
        return result.raw


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

# Alias requeridos por pyproject.toml
kickoff = run


def plot():
    flow = SupervisorFlow()
    flow.plot()


if __name__ == "__main__":
    run()
