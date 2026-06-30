#!/usr/bin/env python
"""
Benchmark de evaluación — OficinaCrew
======================================
Lee evaluation/test_cases.json y ejecuta cada caso contra el sistema,
registrando pass/fail, tiempo de respuesta y tokens usados.

Uso:
    uv run python evaluation/benchmark.py
    uv run python evaluation/benchmark.py --bloque RF1_urgencia
    uv run python evaluation/benchmark.py --id TC-CLAS-05
"""
import argparse
import json
import sys
import time
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "src"))

CASES_FILE = ROOT / "evaluation" / "test_cases.json"
RESULTS_FILE = ROOT / "evaluation" / "results.json"


# ─── Ejecutores por tipo de caso ──────────────────────────────────────────

def _run_rf1_urgencia(caso: dict) -> dict:
    """Ejecuta análisis RF1 sobre un email simulado y compara urgencia."""
    from gmail_monitor import analyze_email_urgency_and_actions
    inp = caso["input"]
    email = {
        "sender": inp.get("remitente", "test@test.com"),
        "subject": inp.get("asunto", ""),
        "body": inp.get("cuerpo", ""),
        "message_id": f"benchmark-{caso['id']}",
    }
    t0 = time.time()
    resultado = analyze_email_urgency_and_actions(email)
    elapsed = round(time.time() - t0, 2)

    esperado = caso["esperado"]
    urgencia_obtenida = resultado.get("urgencia", "")
    passed = urgencia_obtenida == esperado.get("urgencia")

    return {
        "passed": passed,
        "elapsed_s": elapsed,
        "obtenido": {"urgencia": urgencia_obtenida},
        "esperado": esperado,
        "detalle": resultado.get("justificacion_urgencia", ""),
    }


def _run_rf3_tareas(caso: dict) -> dict:
    """Ejecuta análisis RF3 y valida acciones extraídas."""
    from gmail_monitor import analyze_email_urgency_and_actions, has_explicit_scheduling_intent
    inp = caso["input"]
    email = {
        "sender": inp.get("remitente", "test@test.com"),
        "subject": inp.get("asunto", ""),
        "body": inp.get("cuerpo", ""),
        "message_id": f"benchmark-{caso['id']}",
    }
    t0 = time.time()
    resultado = analyze_email_urgency_and_actions(email)
    elapsed = round(time.time() - t0, 2)

    acciones_raw = resultado.get("acciones_pendientes", [])
    # Filtrar como hace el sistema real: excluir citas/calendario
    acciones = [
        a for a in acciones_raw
        if isinstance(a, dict) and a.get("accion", "").strip()
        and not has_explicit_scheduling_intent(a.get("accion", ""))
    ]

    esperado = caso["esperado"]
    checks = []

    if "num_acciones" in esperado:
        ok = len(acciones) == esperado["num_acciones"]
        checks.append(("num_acciones", ok, f"obtenido={len(acciones)}, esperado={esperado['num_acciones']}"))

    if "num_acciones_min" in esperado:
        ok = len(acciones) >= esperado["num_acciones_min"]
        checks.append(("num_acciones_min", ok, f"obtenido={len(acciones)}, mínimo={esperado['num_acciones_min']}"))

    if "accion_contiene" in esperado:
        kw = esperado["accion_contiene"].lower()
        ok = any(kw in a.get("accion", "").lower() for a in acciones)
        checks.append(("accion_contiene", ok, f"buscado='{kw}'"))

    if "tiene_fecha_limite" in esperado and esperado["tiene_fecha_limite"]:
        ok = any(a.get("fecha_limite", "").strip() for a in acciones)
        checks.append(("tiene_fecha_limite", ok, "alguna acción debe tener fecha"))

    passed = all(c[1] for c in checks)
    return {
        "passed": passed,
        "elapsed_s": elapsed,
        "obtenido": {"num_acciones": len(acciones), "acciones": [a.get("accion") for a in acciones]},
        "esperado": esperado,
        "checks": [{"campo": c[0], "ok": c[1], "detalle": c[2]} for c in checks],
    }


def _run_clasificacion(caso: dict) -> dict:
    """Ejecuta clasificación de petición libre y compara categoría."""
    from src.oficinacrew.main import SupervisorFlow
    from gmail_monitor import has_task_query_intent

    peticion = caso["input"]["peticion"]
    esperado = caso["esperado"]

    t0 = time.time()

    # Interceptar tareas igual que hace web_server.py
    if has_task_query_intent(peticion):
        categoria_obtenida = "tareas"
        elapsed = round(time.time() - t0, 2)
        passed = categoria_obtenida == esperado.get("categoria")
        return {
            "passed": passed,
            "elapsed_s": elapsed,
            "obtenido": {"categoria": categoria_obtenida},
            "esperado": esperado,
        }

    flow = SupervisorFlow()
    flow.kickoff(inputs={"peticion": peticion})
    elapsed = round(time.time() - t0, 2)

    clasificacion = flow.state.get("clasificacion", {})
    categoria_obtenida = clasificacion.get("categoria", "")
    passed = categoria_obtenida == esperado.get("categoria")

    return {
        "passed": passed,
        "elapsed_s": elapsed,
        "obtenido": {"categoria": categoria_obtenida},
        "esperado": esperado,
    }


def _run_documentos(caso: dict) -> dict:
    """Ejecuta petición de documentos (RF6/RF7/RF8) y evalúa resultado."""
    from src.oficinacrew.main import SupervisorFlow

    peticion = caso["input"]["peticion"]
    esperado = caso["esperado"]

    t0 = time.time()
    flow = SupervisorFlow()
    result = flow.kickoff(inputs={"peticion": peticion})
    elapsed = round(time.time() - t0, 2)

    texto = str(result).lower()
    checks = []

    if "documento_contiene" in esperado:
        kw = esperado["documento_contiene"].lower()
        ok = kw in texto
        checks.append(("documento_contiene", ok, f"'{kw}' en respuesta"))

    if "resultado_contiene_alguno" in esperado:
        ok = any(kw.lower() in texto for kw in esperado["resultado_contiene_alguno"])
        checks.append(("resultado_contiene_alguno", ok, str(esperado["resultado_contiene_alguno"])))

    if "longitud_min_palabras" in esperado:
        palabras = len(str(result).split())
        ok = palabras >= esperado["longitud_min_palabras"]
        checks.append(("longitud_min_palabras", ok, f"obtenido={palabras}"))

    if "cita_fragmento" in esperado and esperado["cita_fragmento"]:
        ok = ">" in str(result) or "—" in str(result) or '"' in str(result)
        checks.append(("cita_fragmento", ok, "respuesta debe incluir cita textual"))

    passed = all(c[1] for c in checks) if checks else True
    return {
        "passed": passed,
        "elapsed_s": elapsed,
        "obtenido": {"extracto": str(result)[:300]},
        "esperado": esperado,
        "checks": [{"campo": c[0], "ok": c[1], "detalle": c[2]} for c in checks],
    }


EJECUTORES = {
    "RF1_urgencia": _run_rf1_urgencia,
    "RF3_tareas": _run_rf3_tareas,
    "clasificacion_peticion": _run_clasificacion,
    "RF6_busqueda_documento": _run_documentos,
    "RF7_resumen_documento": _run_documentos,
    "RF8_qa_documento": _run_documentos,
}


# ─── Runner principal ──────────────────────────────────────────────────────

def run_benchmark(filtro_bloque: str = None, filtro_id: str = None):
    with open(CASES_FILE, encoding="utf-8") as f:
        casos = json.load(f)

    if filtro_bloque:
        casos = [c for c in casos if c["bloque"] == filtro_bloque]
    if filtro_id:
        casos = [c for c in casos if c["id"] == filtro_id]

    if not casos:
        print("No se encontraron casos con ese filtro.")
        return

    resultados = []
    print(f"\n{'='*65}")
    print(f"  OficinaCrew Benchmark — {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    print(f"  {len(casos)} caso(s) a ejecutar")
    print(f"{'='*65}\n")

    DELAY_ENTRE_CASOS = 5  # segundos entre casos que llaman al LLM (evitar rate limit Groq)
    bloques_sin_llm = {"clasificacion_peticion"}  # interceptados localmente, sin LLM

    for i, caso in enumerate(casos):
        ejecutor = EJECUTORES.get(caso["bloque"])
        if not ejecutor:
            print(f"[SKIP] {caso['id']} — bloque '{caso['bloque']}' sin ejecutor")
            continue

        if i > 0 and caso["bloque"] not in bloques_sin_llm:
            print(f"       (esperando {DELAY_ENTRE_CASOS}s para evitar rate limit...)")
            time.sleep(DELAY_ENTRE_CASOS)

        print(f"[RUN]  {caso['id']} — {caso['descripcion']}")
        try:
            res = ejecutor(caso)
            estado = "✓ PASS" if res["passed"] else "✗ FAIL"
            print(f"       {estado}  ({res['elapsed_s']}s)")
            if not res["passed"]:
                print(f"       esperado : {res['esperado']}")
                print(f"       obtenido : {res['obtenido']}")
        except Exception as e:
            res = {"passed": False, "elapsed_s": 0, "error": str(e)}
            print(f"       ✗ ERROR: {e}")

        resultados.append({
            "id": caso["id"],
            "bloque": caso["bloque"],
            "descripcion": caso["descripcion"],
            **res,
        })
        print()

    # ── Métricas por bloque ───────────────────────────────────────────────
    print(f"\n{'─'*65}")
    print("  RESULTADOS POR BLOQUE")
    print(f"{'─'*65}")
    bloques: dict[str, list] = {}
    for r in resultados:
        bloques.setdefault(r["bloque"], []).append(r)

    total_pass = total_fail = 0
    for bloque, items in bloques.items():
        p = sum(1 for i in items if i.get("passed"))
        f = len(items) - p
        total_pass += p
        total_fail += f
        pct = round(100 * p / len(items)) if items else 0
        t_avg = round(sum(i.get("elapsed_s", 0) for i in items) / len(items), 2)
        print(f"  {bloque:<35} {p}/{len(items)} ({pct}%)  ~{t_avg}s")

    total = total_pass + total_fail
    pct_global = round(100 * total_pass / total) if total else 0
    print(f"{'─'*65}")
    print(f"  TOTAL  {total_pass}/{total} ({pct_global}%)\n")

    # ── Guardar resultados ────────────────────────────────────────────────
    output = {
        "timestamp": datetime.now().isoformat(),
        "total": total,
        "pass": total_pass,
        "fail": total_fail,
        "accuracy_pct": pct_global,
        "por_bloque": {
            b: {
                "pass": sum(1 for i in items if i.get("passed")),
                "total": len(items),
                "accuracy_pct": round(100 * sum(1 for i in items if i.get("passed")) / len(items)),
                "tiempo_medio_s": round(sum(i.get("elapsed_s", 0) for i in items) / len(items), 2),
            }
            for b, items in bloques.items()
        },
        "casos": resultados,
    }
    with open(RESULTS_FILE, "w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, indent=2)
    print(f"  Resultados guardados en evaluation/results.json\n")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Benchmark OficinaCrew")
    parser.add_argument("--bloque", help="Filtrar por bloque (ej: RF1_urgencia)")
    parser.add_argument("--id", help="Ejecutar un solo caso por ID (ej: TC-CLAS-05)")
    args = parser.parse_args()
    run_benchmark(filtro_bloque=args.bloque, filtro_id=args.id)
