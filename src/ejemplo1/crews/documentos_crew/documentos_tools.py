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
    results = []
    for filepath in DOCS_DIR.glob("**/*"):
        if not filepath.is_file() or filepath.suffix.lower() not in SUPPORTED_EXTENSIONS:
            continue
        try:
            content = _read_content(filepath).lower()
            hits = sum(content.count(kw) for kw in keywords)
            if hits > 0:
                results.append((filepath.name, hits))
        except Exception:
            continue
    if not results:
        return "No se encontraron documentos con esas palabras clave."
    results.sort(key=lambda x: x[1], reverse=True)
    return "Documentos encontrados (ordenados por relevancia):\n" + "\n".join(
        f"  - {name}  (coincidencias: {hits})" for name, hits in results
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
