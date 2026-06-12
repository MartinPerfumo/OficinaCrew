import re
from pathlib import Path
from difflib import unified_diff

from crewai.tools import tool

# Directorio donde se almacenan los documentos (raíz del proyecto)
DOCS_DIR = Path(__file__).parent.parent.parent.parent.parent / "documentos"

SUPPORTED_EXTENSIONS = {".txt", ".md", ".pdf"}

# Límite de caracteres por respuesta de tool (~2500 tokens)
# Suficiente para documentos medianos, se trunca si es necesario
MAX_CHARS = 10000


def _read_content(filepath: Path) -> str:
    """Lee el texto de un fichero .txt/.md o .pdf."""
    if filepath.suffix.lower() == ".pdf":
        try:
            from pypdf import PdfReader
            reader = PdfReader(str(filepath))
            pages = [page.extract_text() or "" for page in reader.pages]
            return "\n\n".join(pages)
        except Exception as e:
            return f"Error al leer el PDF: {e}"
    return filepath.read_text(encoding="utf-8")


# ─── Tool 1: Buscar documentos por palabras clave ───────────────────────────

@tool("buscar_documentos")
def buscar_documentos(palabras_clave: str) -> str:
    """Busca documentos en el repositorio por palabras clave.
    
    Args:
        palabras_clave: Palabras clave separadas por espacios o comas
        
    Returns:
        Lista de documentos ordenada por relevancia (número de coincidencias)
    """
    if not DOCS_DIR.exists():
        return f"El directorio de documentos no existe: {DOCS_DIR}"
    keywords = [k.strip().lower() for k in palabras_clave.replace(",", " ").split() if k.strip()]
    query_full = palabras_clave.lower().strip()
    results = []
    for filepath in DOCS_DIR.glob("**/*"):
        if not filepath.is_file() or filepath.suffix.lower() not in SUPPORTED_EXTENSIONS:
            continue
        try:
            content = _read_content(filepath).lower()
            hits = sum(content.count(kw) for kw in keywords)
            if hits > 0:
                normalized = hits / max(len(content) / 1000, 1)
                # Boost si el nombre del archivo coincide con la búsqueda
                name_lower = filepath.stem.lower()
                name_match = 1000 if query_full in name_lower or name_lower in query_full else 0
                name_match += sum(10 for kw in keywords if kw in name_lower)
                results.append((filepath.name, hits, normalized + name_match))
        except Exception:
            continue
    if not results:
        return "No se encontraron documentos con esas palabras clave."
    results.sort(key=lambda x: x[2], reverse=True)
    top = results[:2]
    return "Documentos encontrados (ordenados por relevancia):\n" + "\n".join(
        f"  - {name}  (coincidencias: {hits})" for name, hits, _ in top
    )


# ─── Tool 2: Leer contenido completo de un documento ────────────────────────

@tool("leer_documento")
def leer_documento(nombre_documento: str) -> str:
    """Lee el contenido completo de un documento.
    
    Args:
        nombre_documento: Nombre del archivo a leer, incluyendo extensión (ej: informe.md)
        
    Returns:
        Texto estructurado del documento
    """
    candidates = list(DOCS_DIR.glob(f"**/{nombre_documento}"))
    if not candidates:
        return f"Documento '{nombre_documento}' no encontrado en {DOCS_DIR}."
    try:
        content = _read_content(candidates[0])
        if len(content) > MAX_CHARS:
            content = content[:MAX_CHARS] + f"\n\n... [contenido truncado a {MAX_CHARS} caracteres]"
        return content
    except Exception as e:
        return f"Error al leer el documento: {e}"


# ─── Tool 3: Extraer sección específica de un documento ─────────────────────

@tool("extraer_seccion")
def extraer_seccion(nombre_documento: str, seccion: str) -> str:
    """Extrae una sección específica de un documento buscando por su título o encabezado.
    
    Args:
        nombre_documento: Nombre del archivo (con extensión)
        seccion: Título o nombre de la sección a extraer
        
    Returns:
        Únicamente el contenido de esa sección
    """
    candidates = list(DOCS_DIR.glob(f"**/{nombre_documento}"))
    if not candidates:
        return f"Documento '{nombre_documento}' no encontrado."
    content = _read_content(candidates[0])
    lines = content.splitlines()
    seccion_lower = seccion.lower()

    start = None
    for i, line in enumerate(lines):
        if seccion_lower in line.lower():
            start = i
            break
    if start is None:
        return f"Sección '{seccion}' no encontrada en '{nombre_documento}'."

    # Recoge líneas hasta el siguiente encabezado del mismo nivel o superior
    section_lines = [lines[start]]
    for line in lines[start + 1:]:
        stripped = line.strip()
        if stripped.startswith("#") or (len(stripped) > 2 and set(stripped) <= {"=", "-"}):
            break
        section_lines.append(line)
    result = "\n".join(section_lines)
    if len(result) > MAX_CHARS:
        result = result[:MAX_CHARS] + f"\n\n... [sección truncada a {MAX_CHARS} caracteres]"
    return result


# ─── Tool 4: Comparar dos documentos ────────────────────────────────────────

@tool("comparar_documentos")
def comparar_documentos(documento_a: str, documento_b: str) -> str:
    """Compara dos documentos y devuelve sus diferencias y similitudes en formato diff.
    
    Args:
        documento_a: Nombre del primer documento (con extensión)
        documento_b: Nombre del segundo documento (con extensión)
        
    Returns:
        Diferencias y similitudes en formato diff
    """
    def read_doc(name: str):
        c = list(DOCS_DIR.glob(f"**/{name}"))
        return _read_content(c[0]) if c else None

    content_a = read_doc(documento_a)
    content_b = read_doc(documento_b)

    if content_a is None:
        return f"Documento '{documento_a}' no encontrado."
    if content_b is None:
        return f"Documento '{documento_b}' no encontrado."

    lines_a = content_a.splitlines(keepends=True)
    lines_b = content_b.splitlines(keepends=True)
    diff = list(unified_diff(
        lines_a, lines_b,
        fromfile=documento_a,
        tofile=documento_b,
        lineterm="",
    ))

    if not diff:
        return "Los dos documentos son idénticos."

    output = "\n".join(diff)
    if len(output) > 4000:
        output = output[:4000] + "\n... (diff truncado por longitud)"
    return output


# ─── Tool 5: Responder pregunta concreta con cita de fuente (RF8) ────────────

@tool("buscar_respuesta_en_documento")
def buscar_respuesta_en_documento(nombre_documento: str, pregunta: str) -> str:
    """Busca la respuesta a una pregunta concreta dentro de un documento y devuelve
    los fragmentos más relevantes con el encabezado de sección de origen, listos
    para citar. Indica explícitamente si la información no está en el documento.

    Args:
        nombre_documento: Nombre del archivo a consultar (con extensión, ej: politica_vacaciones.md)
        pregunta: Pregunta concreta cuya respuesta se quiere encontrar en el documento

    Returns:
        Fragmentos relevantes con encabezado de sección para citar, o mensaje explícito
        si la información solicitada no se encuentra en el documento.
    """
    candidates = list(DOCS_DIR.glob(f"**/{nombre_documento}"))
    if not candidates:
        return f"Documento '{nombre_documento}' no encontrado en {DOCS_DIR}."

    try:
        content = _read_content(candidates[0])
    except Exception as e:
        return f"Error al leer el documento: {e}"

    if len(content) > MAX_CHARS:
        content = content[:MAX_CHARS]

    # ── Dividir el documento en secciones por encabezados ────────────────────
    sections: list[tuple[str, str]] = []  # (encabezado, contenido)
    current_heading = "(inicio del documento)"
    current_lines: list[str] = []

    for line in content.splitlines():
        stripped = line.strip()
        if stripped.startswith("#"):
            if current_lines:
                sections.append((current_heading, "\n".join(current_lines).strip()))
            current_heading = stripped.lstrip("#").strip()
            current_lines = []
        else:
            current_lines.append(line)
    if current_lines:
        sections.append((current_heading, "\n".join(current_lines).strip()))

    # ── Puntuar secciones por solapamiento con la pregunta ───────────────────
    pregunta_tokens = [
        t.lower() for t in re.findall(r"[a-záéíóúüñA-ZÁÉÍÓÚÜÑ0-9]+", pregunta)
        if len(t) > 2
    ]
    stop_tokens = {"qué", "que", "cuál", "cual", "cómo", "como", "cuándo",
                   "cuando", "dónde", "donde", "por", "para", "los", "las",
                   "del", "una", "uno", "son", "hay", "tiene", "hay"}
    pregunta_tokens = [t for t in pregunta_tokens if t not in stop_tokens]

    scored: list[tuple[int, str, str]] = []
    for heading, body in sections:
        if not body.strip():
            continue
        combined = (heading + " " + body).lower()
        score = sum(combined.count(tok) for tok in pregunta_tokens)
        if score > 0:
            scored.append((score, heading, body))

    if not scored:
        return (
            f"La información sobre \"{pregunta}\" no se encuentra en el documento "
            f"'{nombre_documento}'. El documento no contiene términos relacionados "
            f"con la pregunta."
        )

    scored.sort(key=lambda x: x[0], reverse=True)
    top = scored[:3]

    # ── Construir respuesta con fragmentos citados ───────────────────────────
    bloques: list[str] = []
    for _, heading, body in top:
        # Truncar fragmento a 600 chars para no sobrepasar contexto
        fragmento = body[:600].rstrip()
        if len(body) > 600:
            fragmento += "..."
        bloques.append(f"**Sección: \"{heading}\"**\n{fragmento}")

    header = (
        f"Fragmentos más relevantes de '{nombre_documento}' "
        f"para la pregunta: \"{pregunta}\"\n\n"
    )
    return header + "\n\n---\n\n".join(bloques)
