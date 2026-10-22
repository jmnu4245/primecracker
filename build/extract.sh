#!/bin/bash
# build/extract.sh
#   ./extract.sh                          -> portable
#   ./extract.sh --with-native            -> además native (online)
#   ./extract.sh --with-native --offline  -> native usando vendor/ (sin red)
set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
VENDOR_DIR="$PROJECT_ROOT/vendor"

ECM_REPO_URL="${ECM_REPO_URL:-https://gitlab.inria.fr/zimmerma/ecm.git}"
YAFU_REPO_URL="${YAFU_REPO_URL:-https://github.com/bbuhrow/yafu.git}"
CADO_REPO_URL="${CADO_REPO_URL:-https://github.com/cado-nfs/cado-nfs.git}"
GMP_TARBALL_URL="${GMP_TARBALL_URL:-https://ftp.gnu.org/gnu/gmp/gmp-6.3.0.tar.xz}"

check_vendor_ready() {
    local missing=0
    for f in "$VENDOR_DIR/ecm.git" "$VENDOR_DIR/yafu.git" "$VENDOR_DIR/cado-nfs.git"; do
        [ -d "$f" ] || { echo "  [!] falta $f"; missing=1; }
    done
    ls "$VENDOR_DIR"/gmp-*.tar.xz >/dev/null 2>&1 || { echo "  [!] falta $VENDOR_DIR/gmp-*.tar.xz"; missing=1; }

    if [ "$missing" -eq 1 ]; then
        echo "[X] vendor/ incompleto. Prepáralo una vez, con red, ANTES del CTF:"
        echo "    git clone --mirror ${ECM_REPO_URL} $VENDOR_DIR/ecm.git"
        echo "    git clone --mirror ${YAFU_REPO_URL} $VENDOR_DIR/yafu.git"
        echo "    git clone --mirror ${CADO_REPO_URL} $VENDOR_DIR/cado-nfs.git"
        echo "    curl -fsSL ${GMP_TARBALL_URL} -o $VENDOR_DIR/gmp-6.3.0.tar.xz"
        exit 1
    fi
}

build_variant() {
    local march="$1"
    local target_dir="$2"
    local offline="$3"
    local image_tag="ctf-builder:${march}"

    # Evita que el COPY del Dockerfile falle si la carpeta aún no existe
    mkdir -p "$VENDOR_DIR"

    local build_args=(--build-arg "MARCH=${march}")
    if [ "$offline" == "offline" ]; then
        check_vendor_ready
        local gmp_local
        gmp_local=$(ls "$VENDOR_DIR"/gmp-*.tar.xz | head -n1)
        build_args+=(
            --build-arg "ECM_REPO_URL=file:///vendor/ecm.git"
            --build-arg "YAFU_REPO_URL=file:///vendor/yafu.git"
            --build-arg "CADO_REPO_URL=file:///vendor/cado-nfs.git"
            --build-arg "GMP_TARBALL_URL=file:///vendor/$(basename "$gmp_local")"
        )
        echo "[*] Modo offline: usando mirrors locales en $VENDOR_DIR"
    else
        build_args+=(
            --build-arg "ECM_REPO_URL=${ECM_REPO_URL}"
            --build-arg "YAFU_REPO_URL=${YAFU_REPO_URL}"
            --build-arg "CADO_REPO_URL=${CADO_REPO_URL}"
            --build-arg "GMP_TARBALL_URL=${GMP_TARBALL_URL}"
        )
    fi

    echo "[*] Construyendo variante '${march}'..."
    docker build --no-cache "${build_args[@]}" -t "${image_tag}" -f "$SCRIPT_DIR/Dockerfile" "$PROJECT_ROOT"

    mkdir -p "$target_dir"
    echo "[*] Extrayendo binarios de '${march}' a ${target_dir}..."
    docker run --rm \
        -v "$SCRIPT_DIR:/shared:z" \
        "${image_tag}" \
        /bin/sh -c "cp /build/ecm/ecm /shared/ecm_bin && \
                    cp /build/yafu/yafu /shared/yafu_bin && \
                    cp -r /build/cado-nfs /shared/cado-nfs_bin && \
                    mkdir -p /shared/cado_libs && \
                    for bin in \$(find /build/cado-nfs/build -type f -executable); do \
                        ldd \"\$bin\" 2>/dev/null | awk '{print \$3}' | grep '^/'; \
                    done | sort -u | while read lib; do \
                        case \"\$lib\" in \
                            */libc.so*|*/libm.so*|*/libpthread.so*|*/librt.so*|*/libdl.so*|*/ld-linux*|*/libresolv*|*/libutil*) ;; \
                            *) cp -n \"\$lib\" /shared/cado_libs/ 2>/dev/null ;; \
                        esac; \
                    done && \
                    chown -R $(id -u):$(id -g) /shared/ecm_bin /shared/yafu_bin /shared/cado-nfs_bin /shared/cado_libs"

    mv "$SCRIPT_DIR/ecm_bin" "$target_dir/ecm_bin"
    mv "$SCRIPT_DIR/yafu_bin" "$target_dir/yafu_bin"
    rm -rf "$target_dir/cado-nfs"
    mv "$SCRIPT_DIR/cado-nfs_bin" "$target_dir/cado-nfs"

    # Librerías que CADO-NFS necesita en runtime y que solo existían
    # dentro del contenedor (p.ej. libhwloc). Sin esto, sus binarios
    # (polyselect, las, bwc...) fallan con "cannot open shared object
    # file" en cualquier máquina que no tenga esas libs instaladas a
    # mano. GNFSWorker debe apuntar LD_LIBRARY_PATH aquí.
    mkdir -p "$target_dir/cado-nfs/lib"
    if [ -n "$(ls -A "$SCRIPT_DIR/cado_libs" 2>/dev/null)" ]; then
        mv "$SCRIPT_DIR"/cado_libs/* "$target_dir/cado-nfs/lib/"
        echo "[*] Librerías bundladas: $(ls "$target_dir/cado-nfs/lib" | tr '\n' ' ')"
    fi
    rm -rf "$SCRIPT_DIR/cado_libs"

    # FIX: no reconstruimos el hostname con un `docker run ... hostname`
    # aparte (cada contenedor tiene un hostname aleatorio distinto, y el que
    # importa es el que estaba activo durante el `make` real). Leemos la
    # carpeta que CADO-NFS creó de verdad bajo cado-nfs/build/.
    local actual_build_dir
    actual_build_dir=$(ls "$target_dir/cado-nfs/build" 2>/dev/null | head -n1)
    if [ -z "$actual_build_dir" ]; then
        echo "[!] No hay ninguna carpeta en cado-nfs/build/ -- ¿falló el make de CADO-NFS?"
        exit 1
    fi
    echo "${actual_build_dir}" > "$target_dir/cado-nfs/.build_hostname"
    echo "[*] Carpeta de build de CADO-NFS: ${actual_build_dir} (guardada en .build_hostname)"

    echo "[+] Variante '${march}' lista en: ${target_dir}"
}

build_variant "x86-64-v3" "$PROJECT_ROOT/bin/portable" "online"

if [ "${1:-}" == "--with-native" ]; then
    if [ "${2:-}" == "--offline" ]; then
        build_variant "native" "$PROJECT_ROOT/bin/native" "offline"
    else
        build_variant "native" "$PROJECT_ROOT/bin/native" "online"
    fi
else
    echo "[i] Build 'native' omitido. Usa --with-native (opcionalmente"
    echo "    --offline) en la máquina real, justo antes de competir."
fi