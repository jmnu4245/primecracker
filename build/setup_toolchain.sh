#!/bin/bash
# build/setup_toolchain.sh

set -u

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
ACTIVE_DIR="$ROOT_DIR/bin/active"

check_binary() {
    local bin_path="$1"
    if [ ! -x "$bin_path" ]; then
        return 1
    fi
    # Se usa -h o se inyecta /dev/null para evitar que el binario 
    # se quede bloqueado esperando stdin si no soporta --version
    "$bin_path" -h >/dev/null 2>&1 < /dev/null
    local exit_code=$?
    if [ "$exit_code" -eq 132 ]; then
        echo "  [!] $(basename "$bin_path"): SIGILL, instrucción no soportada por este CPU."
        return 1
    fi
    return 0
}

try_variant() {
    local variant_dir="$1"
    local label="$2"

    if [ ! -d "$variant_dir" ]; then
        echo "[*] $label: no existe ($variant_dir). Saltando."
        return 1
    fi

    echo "[*] Probando variante '$label' en $variant_dir..."
    
    if check_binary "$variant_dir/ecm_bin" && check_binary "$variant_dir/yafu_bin" \
        && [ -d "$variant_dir/cado-nfs" ]; then
        echo "[+] '$label' es compatible con este CPU."
        
        # Crear un enlace simbólico (symlink) en lugar de copiar recursivamente. 
        # Es instantáneo, atómico y evita duplicar gigabytes de CADO-NFS.
        ln -sfn "$variant_dir" "$ACTIVE_DIR"
        return 0
    fi

    echo "[!] '$label' no es compatible o está incompleta."
    return 1
}

main() {
    echo "[*] Verificando toolchain para $(uname -m) en $(hostname)..."

    if try_variant "$ROOT_DIR/bin/native" "native"; then
        exit 0
    fi

    if try_variant "$ROOT_DIR/bin/portable" "portable"; then
        exit 0
    fi

    echo "[X] Ninguna variante precompilada funciona en este CPU."
    echo "    Ejecuta build/extract.sh en esta máquina para compilarla."
    exit 1
}

main