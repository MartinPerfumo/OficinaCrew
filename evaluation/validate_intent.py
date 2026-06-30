import sys
sys.path.insert(0, ".")
from gmail_monitor import has_task_query_intent

casos = [
    ("Redacta un email a RRHH solicitando los dias de vacaciones pendientes", False),
    ("Que tareas tengo pendientes?", True),
    ("Muestrame mis tareas pendientes", True),
    ("Hay algo pendiente de revisar?", True),
    ("Listar tareas pendientes", True),
    ("Que tengo que hacer?", True),
    ("Cual es la politica de vacaciones pendientes de aplicar?", False),
    ("Solicito los dias pendientes de vacaciones", False),
]

ok = True
for texto, esperado in casos:
    r = has_task_query_intent(texto)
    estado = "OK" if r == esperado else "FALLO"
    print(f"[{estado}]  {'SI' if r else 'NO'}  {texto[:65]}")
    if r != esperado:
        ok = False

print()
print("Todos OK" if ok else "Hay fallos")
