#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
benchmark.py - Suite de benchmarks para el sistema SupervisorFlow (CrewAI)

Modos de ejecucion
------------------
  python benchmark.py                   # Solo clasificacion (rapido, barato)
  python benchmark.py --full            # Clasificacion + ejecucion real de crews
  python benchmark.py --full --caso 0   # Solo el caso nº 0 en modo completo

Metricas recogidas
------------------
  • Latencia de clasificación (ms)
  • Categoría predicha vs esperada  →  clasificación correcta / incorrecta
  • Latencia de ejecución del crew (ms)  [solo en --full]
  • Longitud de la salida (palabras)     [solo en --full]
  • Checks de contenido sobre la salida  [solo en --full]
  • Resumen agregado: precisión, P50/P95 de latencias
"""

import argparse
import contextlib
import io
import json
import statistics
import sys
import time
import warnings
from pathlib import Path

warnings.filterwarnings("ignore", category=SyntaxWarning, module="pysbd")

# Forzar UTF-8 en stdout/stderr para evitar UnicodeEncodeError en Windows (cp1252)
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

# Context manager para silenciar CrewAI
@contextlib.contextmanager
def silence_crewai():
    """Suprime toda la salida de CrewAI (logs, cuadros, etc.)"""
    f = io.StringIO()
    with contextlib.redirect_stdout(f), contextlib.redirect_stderr(f):
        yield

# Aseguramos que el paquete src/ sea importable
sys.path.insert(0, str(Path(__file__).parent / "src"))

# ─── Importaciones del proyecto ────────────────────────────────────────────────
from benchmark_cases import CASOS_CLASIFICACION
from crewai import LLM

# LLM del supervisor (igual que en main.py)
_llm = LLM(model="groq/llama-3.3-70b-versatile")

# ─── Prompt de clasificación (copia fiel de main.py) ──────────────────────────
PROMPT_TEMPLATE = """Eres un clasificador de tareas de oficina. 
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

Responde SOLO con el JSON, sin texto adicional."""


def clasificar(peticion: str) -> tuple[dict, float]:
    """Llama al LLM clasificador y devuelve (resultado_dict, latencia_ms)."""
    prompt = PROMPT_TEMPLATE.format(peticion=peticion)
    t0 = time.perf_counter()
    response = _llm.call(prompt)
    latencia_ms = (time.perf_counter() - t0) * 1000

    text = response if isinstance(response, str) else str(response)
    try:
        start = text.find("{")
        end = text.rfind("}") + 1
        result = json.loads(text[start:end]) if start != -1 and end > start else {}
    except (json.JSONDecodeError, ValueError):
        result = {}

    if "categoria" not in result:
        result = {
            "categoria": "comunicacion",
            "resumen": peticion,
            "texto_agenda": "",
            "texto_comunicacion": peticion,
            "texto_documentos": "",
        }

    return result, latencia_ms


def ejecutar_crew(categoria: str, clasificacion: dict) -> tuple[str, float]:
    """Ejecuta el crew correspondiente y devuelve (salida_raw, latencia_ms)."""
    t0 = time.perf_counter()

    with silence_crewai():
        if categoria == "agenda":
            from ejemplo1.crews.agenda_crew.agenda_crew import AgendaCrew
            result = AgendaCrew().crew().kickoff(
                inputs={"peticion": clasificacion.get("texto_agenda", "")}
            )
            salida = result.raw

        elif categoria == "comunicacion":
            from ejemplo1.crews.comunicacion_crew.comunicacion_crew import ComunicacionCrew
            result = ComunicacionCrew().crew().kickoff(
                inputs={"peticion": clasificacion.get("texto_comunicacion", "")}
            )
            salida = result.raw

        elif categoria == "documentos":
            from ejemplo1.crews.documentos_crew.documentos_crew import DocumentosCrew
            result = DocumentosCrew().crew().kickoff(
                inputs={"peticion": clasificacion.get("texto_documentos", "")}
            )
            salida = result.raw

        elif categoria == "ambos":
            from ejemplo1.crews.agenda_crew.agenda_crew import AgendaCrew
            from ejemplo1.crews.comunicacion_crew.comunicacion_crew import ComunicacionCrew
            r1 = AgendaCrew().crew().kickoff(
                inputs={"peticion": clasificacion.get("texto_agenda", "")}
            )
            r2 = ComunicacionCrew().crew().kickoff(
                inputs={"peticion": clasificacion.get("texto_comunicacion", "")}
            )
            salida = f"{r1.raw}\n\n---\n\n{r2.raw}"

        else:
            salida = ""

    latencia_ms = (time.perf_counter() - t0) * 1000
    return salida, latencia_ms


def verificar_checks(salida: str, checks: list[str]) -> dict:
    """Verifica qué checks de contenido están presentes en la salida."""
    salida_lower = salida.lower()
    resultados = {}
    for check in checks:
        resultados[check] = check.lower() in salida_lower
    return resultados


# ─── Formateo de resultados ────────────────────────────────────────────────────

def _color(texto: str, ok: bool) -> str:
    verde = "\033[92m"
    rojo = "\033[91m"
    reset = "\033[0m"
    return f"{verde if ok else rojo}{texto}{reset}"


def imprimir_resultado_caso(idx: int, caso: dict, clasificacion: dict,
                             lat_cls: float, salida: str | None,
                             lat_crew: float | None, modo_full: bool):
    correcto = clasificacion.get("categoria") == caso["categoria"]
    estado = "[OK]" if correcto else "[FAIL]"

    print(f"\n[{idx+1:02d}] {caso['descripcion']}")
    print(f"     Esperado: {caso['categoria']:15s}  Obtenido: {clasificacion.get('categoria','?'):15s}  {estado}")
    print(f"     Lat.cls: {lat_cls:7.0f}ms", end="")

    if modo_full and salida is not None and lat_crew is not None:
        palabras = len(salida.split())
        print(f"  |  Lat.crew: {lat_crew:7.0f}ms  |  Palabras: {palabras:4d}", end="")

        checks = caso.get("checks_salida", [])
        if checks:
            resultados = verificar_checks(salida, checks)
            ok_checks = all(resultados.values())
            estado_checks = "PASS" if ok_checks else "FAIL"
            print(f"  |  Checks: {estado_checks}", end="")

    print()


def imprimir_resumen(resultados: list[dict], modo_full: bool):
    total = len(resultados)
    correctos = sum(1 for r in resultados if r["correcto"])
    precision = correctos / total * 100 if total else 0

    print(f"\n{'=' * 80}")
    print(f"  RESUMEN")
    print(f"{'=' * 80}")
    print(f"  Precision clasificacion: {correctos}/{total}  ({precision:.1f}%)")

    lats_cls = [r["lat_clasificacion_ms"] for r in resultados]
    print(f"  Latencia clasificacion (ms): min={min(lats_cls):.0f}, p50={statistics.median(lats_cls):.0f}, p95={sorted(lats_cls)[int(len(lats_cls)*0.95)]:.0f}, max={max(lats_cls):.0f}")

    if modo_full:
        lats_crew = [r["lat_crew_ms"] for r in resultados if r.get("lat_crew_ms") is not None and r.get("lat_crew_ms", 0) > 0]
        if lats_crew:
            print(f"  Latencia crew (ms):        min={min(lats_crew):.0f}, p50={statistics.median(lats_crew):.0f}, p95={sorted(lats_crew)[int(len(lats_crew)*0.95)]:.0f}, max={max(lats_crew):.0f}")

        checks_totales = sum(r.get("checks_total", 0) for r in resultados)
        checks_ok = sum(r.get("checks_ok", 0) for r in resultados)
        if checks_totales:
            print(f"  Checks contenido:          {checks_ok}/{checks_totales}  ({checks_ok/checks_totales*100:.1f}%)")

    errores = [r for r in resultados if not r["correcto"]]
    if errores:
        print(f"\n  Errores:")
        for e in errores:
            print(f"    * {e['descripcion']}: esperado={e['esperado']}, obtenido={e['obtenido']}")

    print(f"{'=' * 80}\n")


# ─── Runner principal ─────────────────────────────────────────────────────────

def run_benchmarks(modo_full: bool = False, solo_caso: int | None = None):
    casos = CASOS_CLASIFICACION
    if solo_caso is not None:
        if solo_caso < 0 or solo_caso >= len(casos):
            print(f"Error: --caso debe estar entre 0 y {len(casos)-1}")
            sys.exit(1)
        casos = [casos[solo_caso]]

    modo_str = "COMPLETO" if modo_full else "CLASIFICACION"
    print(f"\n{'=' * 80}")
    print(f"  BENCHMARK SupervisorFlow  -  {modo_str}")
    print(f"  {len(casos)} casos  |  groq/llama-3.3-70b-versatile")
    print(f"{'=' * 80}")

    resultados = []

    for idx, caso in enumerate(casos):
        print(f"  [{idx+1:2d}] {caso['descripcion']:<40s}", end=" ", flush=True)

        # 1. Clasificacion
        clasificacion, lat_cls = clasificar(caso["peticion"])

        # 2. Crew (opcional)
        salida = None
        lat_crew = None
        checks_total = 0
        checks_ok_count = 0

        if modo_full:
            categoria_pred = clasificacion.get("categoria", "comunicacion")
            try:
                salida, lat_crew = ejecutar_crew(categoria_pred, clasificacion)
            except Exception as exc:
                salida = f"[ERROR al ejecutar crew: {exc}]"
                lat_crew = 0.0

            checks = caso.get("checks_salida", [])
            checks_total = len(checks)
            if checks and salida:
                resultado_checks = verificar_checks(salida, checks)
                checks_ok_count = sum(1 for v in resultado_checks.values() if v)

        correcto = clasificacion.get("categoria") == caso["categoria"]

        registro = {
            "descripcion": caso["descripcion"],
            "peticion": caso["peticion"],
            "esperado": caso["categoria"],
            "obtenido": clasificacion.get("categoria", "?"),
            "correcto": correcto,
            "lat_clasificacion_ms": lat_cls,
            "lat_crew_ms": lat_crew,
            "checks_total": checks_total,
            "checks_ok": checks_ok_count,
        }
        resultados.append(registro)

        # Print resultado en linea compacta
        estado = "OK" if correcto else "FAIL"
        print(f"  {clasificacion.get('categoria'):15s}  {lat_cls:6.0f}ms  {estado}")

    imprimir_resumen(resultados, modo_full)

    # Guardar JSON
    output_path = Path(__file__).parent / "benchmark_results.json"
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(resultados, f, ensure_ascii=False, indent=2)
    print(f"  Resultados guardados en: {output_path}\n")

    # Codigo de salida
    total = len(resultados)
    correctos = sum(1 for r in resultados if r["correcto"])
    sys.exit(0 if (correctos / total >= 0.8 if total else True) else 1)


# ─── CLI ──────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="Benchmark del sistema SupervisorFlow (CrewAI).",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Ejemplos:
  python benchmark.py                   # Solo clasificación (rápido)
  python benchmark.py --full            # Clasificación + ejecución de crews
  python benchmark.py --full --caso 0   # Solo el caso 0 en modo completo
        """,
    )
    parser.add_argument(
        "--full",
        action="store_true",
        help="Ejecuta también los crews (requiere API key activa y tarda más).",
    )
    parser.add_argument(
        "--caso",
        type=int,
        default=None,
        metavar="N",
        help="Ejecuta solo el caso nº N (base 0).",
    )
    args = parser.parse_args()
    run_benchmarks(modo_full=args.full, solo_caso=args.caso)


if __name__ == "__main__":
    main()
