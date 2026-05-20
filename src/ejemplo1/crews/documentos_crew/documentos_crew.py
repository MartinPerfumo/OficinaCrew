from crewai import Agent, Crew, Process, Task, LLM
from crewai.project import CrewBase, agent, crew, task

from ejemplo1.crews.documentos_crew.documentos_tools import (
    buscar_documentos,
    leer_documento,
    extraer_seccion,
    comparar_documentos,
)

# Usar el mismo modelo que el supervisor para consistencia
# El contenido está truncado a 4000 chars para evitar límites de TPM
llm = LLM(model="groq/llama-3.3-70b-versatile")


@CrewBase
class DocumentosCrew:
    """Crew especializado en análisis y recuperación de documentos."""

    agents_config = "config/agents.yaml"
    tasks_config = "config/tasks.yaml"

    @agent
    def agente_documentos(self) -> Agent:
        return Agent(
            config=self.agents_config["agente_documentos"],
            llm=llm,
            tools=[
                buscar_documentos,
                leer_documento,
                extraer_seccion,
                comparar_documentos,
            ],
            verbose=True,
            max_iter=15,
            allow_delegation=False,
        )

    @task
    def tarea_documentos(self) -> Task:
        return Task(
            config=self.tasks_config["tarea_documentos"],
        )

    @crew
    def crew(self) -> Crew:
        return Crew(
            agents=self.agents,
            tasks=self.tasks,
            process=Process.sequential,
            verbose=True,
        )
