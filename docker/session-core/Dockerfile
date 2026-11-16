FROM local/virtual-alpide:native
# SPDX-License-Identifier: LGPL-3.0-or-later
COPY session_core /opt/public-work/session_core
COPY integration/__init__.py integration/trigger_logic.py /opt/public-work/integration/
COPY tracksim/src /opt/public-work/tracksim-src
ENV PYTHONPATH=/opt/public-work/virtual_alpide:/opt/public-work:/opt/public-work/tracksim-src:/opt/eudaq/lib:/opt/eudaq-src/user/ITS3/python
COPY eudaq_module /opt/eudaq-src/user/msensor
COPY docker/base/build-user-module.sh /tmp/build-user-module.sh
RUN /tmp/build-user-module.sh msensor
