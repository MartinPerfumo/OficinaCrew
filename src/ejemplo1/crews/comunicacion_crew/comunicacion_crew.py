from crewai import Agent, Crew, Process, Task, LLM
from crewai.agents.agent_builder.base_agent import BaseAgent
from crewai.project import CrewBase, agent, crew, task

llm = LLM(model="groq/llama-3.3-70b-versatile")

@CrewBase
class ComunicacionCrew:
    """Crew especializado en comunicación."""

    agents_config = "config/agents.yaml"
    tasks_config = "config/tasks.yaml"

  
    @agent
    def agente_comunicacion(self) -> Agent:
        return Agent(
            config=self.agents_config["agente_comunicacion"],
            llm=llm,
            verbose=True,
        )

    @task
    def tarea_comunicacion(self) -> Task:
        return Task(
            config=self.tasks_config["tarea_comunicacion"],  # type: ignore[index]
        )


    @crew
    def crew(self) -> Crew:

        return Crew(
            agents=self.agents,  # Automatically created by the @agent decorator
            tasks=self.tasks,  # Automatically created by the @task decorator
            process=Process.sequential,
            verbose=True,
        )
