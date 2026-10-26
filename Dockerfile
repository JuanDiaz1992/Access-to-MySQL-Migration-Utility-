# --- Etapa 1: mdbtools 1.0.0 (la misma versión de Ubuntu 24.04) compilado con los decimales completos ---
# mdb-export escribe los Single con "%.8g" y los Double con "%.16lg": con eso algunos valores no vuelven a ser el mismo
# número binario al cargarlos en MySQL (p. ej. el Double 2315.2199999999998 de Access llegaba como 2315.22). Con 9 cifras
# (Single) y 17 (Double) el texto identifica de forma única el valor de Access, que queda idéntico bit a bit en MySQL.
FROM ubuntu:24.04 AS mdbtools
ARG MDBTOOLS_VERSION=1.0.0
RUN apt-get update \
 && DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \
      build-essential pkg-config libglib2.0-dev bison flex gawk ca-certificates curl \
 && rm -rf /var/lib/apt/lists/*
WORKDIR /src
RUN curl -fsSL "https://github.com/mdbtools/mdbtools/releases/download/v${MDBTOOLS_VERSION}/mdbtools-${MDBTOOLS_VERSION}.tar.gz" \
      | tar xz --strip-components=1
# Falla la construcción si el formato original ya no es el esperado (otra versión de mdbtools): no se parchea a ciegas
RUN grep -q '"%.8g", tf' src/libmdb/data.c && grep -q '"%.16lg", td' src/libmdb/data.c \
 && sed -i 's/"%\.8g", tf/"%.9g", tf/; s/"%\.16lg", td/"%.17lg", td/' src/libmdb/data.c \
 && grep -n '"%.9g", tf\|"%.17lg", td' src/libmdb/data.c
RUN ./configure --prefix=/opt/mdbtools --disable-man --disable-gtk-doc \
 && make -j"$(nproc)" && make install

# --- Etapa 2: la imagen del migrador ---
FROM ubuntu:24.04

RUN apt-get update \
 && DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \
      python3 python3-venv libglib2.0-0t64 mysql-client ca-certificates \
 && rm -rf /var/lib/apt/lists/*

COPY --from=mdbtools /opt/mdbtools /opt/mdbtools

WORKDIR /app
COPY requirements.txt .
RUN python3 -m venv /opt/venv && /opt/venv/bin/pip install --no-cache-dir -r requirements.txt

ENV PATH=/opt/mdbtools/bin:/opt/venv/bin:$PATH LD_LIBRARY_PATH=/opt/mdbtools/lib PYTHONUNBUFFERED=1 PYTHONIOENCODING=utf-8 LANG=C.UTF-8 LC_ALL=C.UTF-8
COPY *.py ./
RUN mkdir -p bases_datos

CMD ["bash"]
