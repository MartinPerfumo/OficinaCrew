#!/usr/bin/env python
"""
setup_gmail.py - Configura autenticación OAuth2 para Gmail Monitor

Este script obtiene credenciales de Google Cloud para permitir que
gmail_monitor.py acceda a tu bandeja de entrada.

Pasos:
  1. Ve a https://console.cloud.google.com
  2. Crea un proyecto nuevo (o usa uno existente)
  3. Habilita la Gmail API
  4. Crea credenciales de aplicación de escritorio (OAuth 2.0)
  5. Descarga el archivo JSON y guárdalo como ~/.gmail_credentials.json
  6. Ejecuta este script: python setup_gmail.py
  7. Autoriza la aplicación en el navegador
  8. Se guardará el token en ~/.gmail_token.json

Referencia:
  https://developers.google.com/gmail/api/quickstart/python
"""

import json
import sys
from pathlib import Path

try:
    from google_auth_oauthlib.flow import InstalledAppFlow
    from google.auth.transport.requests import Request
except ImportError:
    print("Error: Dependencias no instaladas.")
    print("Ejecuta: uv add 'google-auth-oauthlib>=1.1.0' 'google-auth-httplib2>=0.2.0'")
    sys.exit(1)

CREDENTIALS_FILE = Path.home() / ".gmail_credentials.json"
TOKEN_FILE = Path.home() / ".gmail_token.json"
SCOPES = [
    "https://www.googleapis.com/auth/gmail.readonly",
    "https://www.googleapis.com/auth/gmail.modify",
    "https://www.googleapis.com/auth/gmail.send",
    "https://www.googleapis.com/auth/calendar.events",
]

def main():
    print("\n" + "=" * 70)
    print("  Gmail OAuth2 Setup")
    print("=" * 70)

    # Verificar archivo de credenciales
    if not CREDENTIALS_FILE.exists():
        print(f"\n[ERROR] Archivo de credenciales no encontrado:")
        print(f"        {CREDENTIALS_FILE}")
        print("\nPasos para obtenerlo:")
        print("  1. Ve a: https://console.cloud.google.com/apis/dashboard")
        print("  2. Crea un proyecto nuevo (o usa uno existente)")
        print("  3. Habilita: Gmail API")
        print("  4. Crea credenciales: Aplicacion de escritorio (OAuth 2.0)")
        print("  5. Descarga el JSON (credentials.json)")
        print(f"  6. Cópialo aquí: {CREDENTIALS_FILE}")
        print("\nReferencia: https://developers.google.com/gmail/api/quickstart/python")
        sys.exit(1)

    print(f"\n[OK] Archivo de credenciales encontrado: {CREDENTIALS_FILE}")

    # Obtener credenciales
    print("\n[*] Iniciando flujo de autenticación OAuth2...")
    print("    Se abrirá un navegador para que autorices Gmail y Google Calendar")
    print("    Selecciona tu cuenta de Google y haz clic en 'Permitir'")

    flow = InstalledAppFlow.from_client_secrets_file(CREDENTIALS_FILE, SCOPES)
    creds = flow.run_local_server(port=0)

    # Guardar token
    with open(TOKEN_FILE, "w") as token:
        token.write(creds.to_json())

    print(f"\n[OK] Credenciales guardadas en: {TOKEN_FILE}")
    print("\nAhora puedes ejecutar:")
    print("  uv run python gmail_monitor.py          # Escuchar en background + crear eventos")
    print("  uv run python gmail_monitor.py --test   # Test rápido")
    print("\n" + "=" * 70 + "\n")

if __name__ == "__main__":
    main()
