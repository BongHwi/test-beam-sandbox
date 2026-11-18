# check=skip=InvalidDefaultArgInFrom
# SPDX-License-Identifier: LGPL-3.0-or-later
ARG SESSION_IMAGE
ARG BASE_IMAGE
FROM ${BASE_IMAGE} AS build
ARG TARGETARCH
SHELL ["/bin/bash", "-o", "pipefail", "-c"]
ENV PATH="/opt/build/tools/bin:/opt/nestdaq/bin:${PATH}" LD_LIBRARY_PATH="/opt/nestdaq/lib:/opt/conda/lib:${LD_LIBRARY_PATH}" CMAKE_BUILD_PARALLEL_LEVEL=4
COPY docker/nestdaq/build.sh docker/nestdaq/preload.cmake docker/nestdaq/apt-build.lock /opt/build/config/
COPY docker/nestdaq/filesink/ /opt/build/config/filesink/
COPY docker/base/update-index.sh /opt/build/update-index.sh
RUN mkdir -p /opt/build-logs && /opt/build/config/build.sh tools > /opt/build-logs/tools.log 2>&1 || { tail -200 /opt/build-logs/tools.log; exit 1; }
RUN /opt/build/config/build.sh aptdeps > /opt/build-logs/aptdeps.log 2>&1 || { tail -200 /opt/build-logs/aptdeps.log; exit 1; }
RUN /opt/build/config/build.sh sources > /opt/build-logs/sources.log 2>&1 || { tail -200 /opt/build-logs/sources.log; exit 1; }
RUN /opt/build/config/build.sh hiredis > /opt/build-logs/hiredis.log 2>&1 || { tail -200 /opt/build-logs/hiredis.log; exit 1; }
RUN /opt/build/config/build.sh external > /opt/build-logs/external.log 2>&1 || { tail -200 /opt/build-logs/external.log; exit 1; }
RUN /opt/build/config/build.sh boost_iostreams > /opt/build-logs/boost_iostreams.log 2>&1 || { tail -200 /opt/build-logs/boost_iostreams.log; exit 1; }
RUN /opt/build/config/build.sh redis > /opt/build-logs/redis.log 2>&1 || { tail -200 /opt/build-logs/redis.log; exit 1; }
RUN /opt/build/config/build.sh timeseries > /opt/build-logs/timeseries.log 2>&1 || { tail -200 /opt/build-logs/timeseries.log; exit 1; }
RUN /opt/build/config/build.sh nestdaq > /opt/build-logs/nestdaq.log 2>&1 || { tail -200 /opt/build-logs/nestdaq.log; exit 1; }
RUN /opt/build/config/build.sh filesink > /opt/build-logs/filesink.log 2>&1 || { tail -200 /opt/build-logs/filesink.log; exit 1; }
COPY docker/nestdaq/slowdash-requirements.lock /opt/build/config/
RUN /opt/build/config/build.sh slowdash > /opt/build-logs/slowdash.log 2>&1 || { tail -200 /opt/build-logs/slowdash.log; exit 1; }
COPY docker/nestdaq/versions.lock /opt/build/config/
RUN /opt/build/config/build.sh licenses > /opt/build-logs/licenses.log 2>&1 || { tail -200 /opt/build-logs/licenses.log; exit 1; }
RUN /opt/build/config/build.sh check > /opt/build-logs/check.log 2>&1 || { tail -200 /opt/build-logs/check.log; exit 1; }
COPY nestdaq_mode/devices/ /opt/build/devices/
RUN { cmake -S /opt/build/devices -B /opt/build/work/devices -DCMAKE_PREFIX_PATH='/opt/nestdaq;/opt/conda' -DCMAKE_BUILD_TYPE=RelWithDebInfo -DCMAKE_CXX_COMPILER=/opt/conda/bin/c++ && cmake --build /opt/build/work/devices --target MSensorFileSource MSensorStreamServer MSensorDqm --parallel 4 && cp /opt/build/work/devices/MSensorFileSource /opt/build/work/devices/MSensorStreamServer /opt/build/work/devices/MSensorDqm /opt/nestdaq/bin/; } > /opt/build-logs/devices.log 2>&1 || { tail -200 /opt/build-logs/devices.log; exit 1; }
FROM ${SESSION_IMAGE}
COPY --from=build /opt/nestdaq /opt/nestdaq
COPY --from=build /opt/slowdash /opt/slowdash
ENV PATH="/opt/nestdaq/bin:${PATH}" LD_LIBRARY_PATH="/opt/nestdaq/lib:${LD_LIBRARY_PATH}"
COPY nestdaq_mode /opt/public-work/nestdaq_mode
