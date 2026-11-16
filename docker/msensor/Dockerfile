FROM local/public-stack:native
# SPDX-License-Identifier: LGPL-3.0-or-later
WORKDIR /work
COPY msensor /work/msensor
COPY tests/e2e/msensor_device.py /work/tests/e2e/msensor_device.py
COPY tests/e2e/msensor_fixtures /work/tests/e2e/msensor_fixtures
ENV PYTHONPATH="/work:/opt/eudaq/lib:/opt/eudaq-src/user/ITS3/python"
COPY eudaq_module /opt/eudaq-src/user/msensor
COPY docker/base/build-user-module.sh /tmp/build-user-module.sh
RUN /tmp/build-user-module.sh msensor
COPY tests/e2e/msensor_eudaq.py /work/tests/e2e/msensor_eudaq.py
COPY tests/e2e/eudaq_timing.cc /work/tests/e2e/eudaq_timing.cc
RUN c++ -std=c++17 -shared -fPIC /work/tests/e2e/eudaq_timing.cc -ldl -o /work/tests/e2e/eudaq_timing.so
COPY tests/e2e/msensor_convert.cc /work/tests/e2e/msensor_convert.cc
RUN c++ -std=c++17 -I/opt/eudaq/include /work/tests/e2e/msensor_convert.cc -L/opt/eudaq/lib -leudaq_core -ldl -Wl,-rpath,/opt/eudaq/lib -o /work/tests/e2e/msensor_convert
COPY tests/e2e/msensor_corry.py /work/tests/e2e/msensor_corry.py
COPY tests/e2e/msensor_fault_producer.py /work/tests/e2e/msensor_fault_producer.py
