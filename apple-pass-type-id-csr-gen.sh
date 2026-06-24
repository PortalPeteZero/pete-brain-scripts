#!/bin/bash
# Generate Certificate Signing Request (CSR) for Apple Pass Type ID.
#
# Outputs:
#   Library/processes/secrets/apple-pass-type-id-private.key       (RSA 2048 private key, mode 600)
#   Library/processes/secrets/apple-pass-type-id.certSigningRequest (CSR to upload to Apple)
#
# Run once per Pass Type ID. Apple requires RSA (not ECDSA) for Pass Type certs.
#
# Re-running overwrites the existing key + CSR — only do that if you're
# starting fresh on a Pass Type cert rotation.
#
# Step 2 of the BYOI plan at Projects/SY-Digital-Certs/files/apple-byoi-setup.md.

set -euo pipefail

CERT_DIR="$(cd "$(dirname "$0")/../secrets" && pwd)"
KEY_FILE="$CERT_DIR/apple-pass-type-id-private.key"
CSR_FILE="$CERT_DIR/apple-pass-type-id.certSigningRequest"

if [[ -f "$KEY_FILE" || -f "$CSR_FILE" ]]; then
  echo "WARNING: existing key or CSR found at:"
  [[ -f "$KEY_FILE" ]] && echo "  $KEY_FILE"
  [[ -f "$CSR_FILE" ]] && echo "  $CSR_FILE"
  echo "Re-running will overwrite. Aborting. Move or delete the old files first if you intend to regenerate."
  exit 1
fi

# 1) Generate 2048-bit RSA private key
openssl genrsa -out "$KEY_FILE" 2048
chmod 600 "$KEY_FILE"

# 2) Generate CSR
openssl req -new -key "$KEY_FILE" \
  -out "$CSR_FILE" \
  -subj "/emailAddress=pete.ashcroft@sygma-solutions.com/CN=Sygma Solutions Ltd Pass Type ID/O=Sygma Solutions Ltd/C=GB"

echo
echo "CSR ready at:        $CSR_FILE"
echo "Private key (SAFE)   $KEY_FILE  (mode 600)"
echo
echo "Next: upload the .certSigningRequest file in Apple Developer portal"
echo "      (Identifiers -> Pass Type IDs -> click into Sygma Digital Certs"
echo "      -> Create Certificate)"
