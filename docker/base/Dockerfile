# check=skip=InvalidDefaultArgInFrom
# SPDX-License-Identifier: LGPL-3.0-or-later
ARG ROOT_IMAGE
FROM ${ROOT_IMAGE} AS eudaq
ARG TARGETARCH
SHELL ["/bin/bash", "-o", "pipefail", "-c"]
ENV DEBIAN_FRONTEND=noninteractive
COPY docker/base/apt-*.lock /tmp/
COPY docker/base/update-index.sh /tmp/update-index.sh
COPY docker/base/pip-constraints.lock /tmp/pip-constraints.lock
COPY docker/base/conda-amd64.explicit /tmp/conda-amd64.explicit
COPY docker/base/conda-amd64.sha256 /tmp/conda-amd64.sha256
RUN test -f "/tmp/apt-${TARGETARCH}.lock" && \
    cat /tmp/apt-packages.lock "/tmp/apt-${TARGETARCH}.lock" > /tmp/apt-native.lock && \
    bash /tmp/update-index.sh && xargs -r -a /tmp/apt-native.lock apt-get install -y --no-install-recommends && rm -rf /var/lib/apt/lists/*
RUN if [ "$TARGETARCH" = amd64 ]; then \
      CONDA_PKGS_DIRS=/tmp/conda-pkgs mamba create -y -p /opt/conda-native --file /tmp/conda-amd64.explicit && \
      (cd /tmp/conda-pkgs && sha256sum -c /tmp/conda-amd64.sha256) && \
      mv /opt/conda /opt/conda-legacy && ln -s /opt/conda-native /opt/conda && \
      rm -rf /tmp/conda-pkgs; \
    fi
RUN git clone https://github.com/eudaq/eudaq.git /opt/eudaq-src && \
    git -C /opt/eudaq-src checkout bfe26c7b73844344b2cbfa37ab853f2a0cb1f23e && \
    test "$(git -C /opt/eudaq-src rev-parse HEAD)" = bfe26c7b73844344b2cbfa37ab853f2a0cb1f23e
RUN python -m pip install -c /tmp/pip-constraints.lock -r /opt/eudaq-src/user/ITS3/requirements.txt
RUN cmake -S /opt/eudaq-src -B /opt/eudaq-build -DEUDAQ_INSTALL_PREFIX=/opt/eudaq -DPython_EXECUTABLE=/opt/conda/bin/python -DPython_INCLUDE_DIR=/opt/conda/include/python3.11 -DPython_LIBRARY=/opt/conda/lib/libpython3.11.so -DEUDAQ_BUILD_PYTHON=ON -DUSER_ITS3_BUILD=ON -DUSER_EXAMPLE_BUILD=ON -DEUDAQ_BUILD_GUI=OFF -DCMAKE_BUILD_TYPE=Release && \
    test "$(git -C /opt/eudaq-build/_deps/extern_jsoncons-src rev-parse HEAD)" = 95a220dff2d927f15e1fe0c7dac2821b7134e4c9 && \
    cmake --build /opt/eudaq-build --parallel 4 && cmake --install /opt/eudaq-build
ENV PATH="/opt/eudaq/bin:${PATH}" PYTHONPATH="/opt/eudaq/lib:/opt/eudaq-src/user/ITS3/python" LD_LIBRARY_PATH="/opt/eudaq/lib:/opt/conda/lib"
# A pinned conda environment need not contain the conda shell integration.
COPY docker/base/entrypoint.sh /entry_point.sh
RUN chmod 755 /entry_point.sh && \
    if [ -f /root/.bashrc ]; then \
      sed -i 's|^\. /opt/conda/etc/profile.d/conda.sh && conda activate base$|if [ -r /opt/conda/etc/profile.d/conda.sh ]; then . /opt/conda/etc/profile.d/conda.sh \&\& conda activate base; fi|' /root/.bashrc; \
    fi
FROM eudaq AS full
COPY docker/versions.lock /tmp/versions.lock
RUN corry_commit="$(awk -F '|' '$1 ~ /^Corryvreckan[[:space:]]*$/ {gsub(/[[:space:]]/, "", $3); print $3}' /tmp/versions.lock)" && \
    test "$corry_commit" = 22fca3e43ceec7ba6263a5cf179cd57d5fff42fe && \
    git clone https://gitlab.cern.ch/corryvreckan/corryvreckan.git /opt/corry-src && \
    git -C /opt/corry-src checkout --detach "$corry_commit" && \
    test "$(git -C /opt/corry-src rev-parse HEAD)" = "$corry_commit"
COPY docker/base/corry-root-eigen.patch /tmp/corry-root-eigen.patch
RUN git -C /opt/corry-src apply /tmp/corry-root-eigen.patch
RUN cmake -S /opt/corry-src -B /opt/corry-build -DCMAKE_INSTALL_PREFIX=/opt/corry -DCMAKE_PREFIX_PATH='/opt/conda;/opt/eudaq' -DBUILD_EventLoaderEUDAQ2=ON -DCMAKE_BUILD_TYPE=Release && \
    cmake --build /opt/corry-build --parallel 4 && cmake --install /opt/corry-build
ENV PATH="/opt/corry/bin:/opt/eudaq/bin:${PATH}" PYTHONPATH="/opt/eudaq/lib:/opt/eudaq-src/user/ITS3/python" LD_LIBRARY_PATH="/opt/eudaq/lib:/opt/conda/lib"
