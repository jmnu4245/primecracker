FROM ubuntu:22.04
ENV DEBIAN_FRONTEND=noninteractive

ARG MARCH=x86-64-v3

ARG GMP_TARBALL_URL=https://ftp.gnu.org/gnu/gmp/gmp-6.3.0.tar.xz
ARG ECM_REPO_URL=https://gitlab.inria.fr/zimmerma/ecm.git
ARG YAFU_REPO_URL=https://github.com/bbuhrow/yafu.git
ARG CADO_REPO_URL=https://github.com/cado-nfs/cado-nfs.git

# Commits fijos: son los de los binarios que funcionan en bin/ (GMP-ECM
# 7.0.7, YAFU 3.1.9, CADO-NFS del 2026-09-24). Con `master` un rebuild
# compilaba lo que hubiera ese día. Para actualizar: cambiar aquí, volver a
# medir con benchmark/run_benchmark.py y pasar los tests de integración.
ARG ECM_REF=6226d2d752f07ca183e88bcc1fd5478dfee1ccd6
ARG YAFU_REF=963dbe9c45283cc06e9a71d830b0676bc1b0d343
ARG CADO_REF=bfad8c720a71b9ae3efe5516e59245ccc29449fe
ARG GMP_SHA256=a3c2b80201b89e68616f4ad30bc66aee4927c3ce50e33929ca819d5c43538898

RUN apt update && apt install -y \
    build-essential git autoconf libtool libgomp1 cmake m4 curl xz-utils \
    python3 python3-distutils python3-requests python3-flask \
    zlib1g-dev libhwloc-dev

WORKDIR /build
RUN git config --global protocol.file.allow always
# Copiamos la carpeta vendor/ local (si existe) dentro del contenedor.
# Si el script bash llamó al build en modo offline, las URLs file:/// apuntarán aquí.
COPY vendor /vendor

RUN curl -fsSL "${GMP_TARBALL_URL}" -o gmp.tar.xz && \
    echo "${GMP_SHA256}  gmp.tar.xz" | sha256sum -c - && \
    tar xf gmp.tar.xz && \
    mv gmp-*/ gmp && \
    cd gmp && \
    ./configure --enable-fat --enable-static --disable-shared \
        CFLAGS="-O3 -march=${MARCH}" && \
    make -j$(nproc) && make install

RUN git clone "${ECM_REPO_URL}" ecm && \
    cd ecm && git checkout ${ECM_REF} && \
    autoreconf -i && \
    ./configure --enable-static --disable-shared LDFLAGS="-static" \
        CFLAGS="-O3 -march=${MARCH}" \
        --with-gmp=/usr/local && \
    make -j$(nproc) && make install

RUN git clone "${YAFU_REPO_URL}" yafu && \
    cd yafu && git checkout ${YAFU_REF} && \
    cp config.mk.example config.mk && \
    echo "CFLAGS+=-O3 -march=${MARCH}" >> config.mk && \
    echo "GMP_INCDIR=/usr/local/include" >> config.mk && \
    echo "GMP_LIBDIR=/usr/local/lib" >> config.mk && \
    echo "ECM_INCDIR=/usr/local/include" >> config.mk && \
    echo "ECM_LIBDIR=/usr/local/lib" >> config.mk && \
    make yafu ECM=1 OMP=1 -j$(nproc) LDFLAGS="-static-libgomp"

RUN rm -f /usr/local/lib/libecm* /usr/local/include/ecm.h && \
    git clone "${CADO_REPO_URL}" cado-nfs && \
    cd cado-nfs && git checkout ${CADO_REF} && \
    make -j$(nproc)