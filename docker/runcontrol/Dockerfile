FROM local/public-stack:native
# SPDX-License-Identifier: LGPL-3.0-or-later
COPY docker/base/update-index.sh /tmp/update-index.sh
RUN bash /tmp/update-index.sh && DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends tmux=3.0a-2ubuntu0.4 libevent-2.1-7=2.1.11-stable-1 libutempter0=1.1.6-4 htop=2.2.0-2build1 && rm -rf /var/lib/apt/lists/*
# Same as docker/setup/Dockerfile: the public launcher's log and perf window programs.
RUN test -x /opt/eudaq/bin/euCliLogger && ln -s /opt/eudaq/bin /opt/eudaq-src/bin
RUN printf '#!/bin/sh\nunset LD_LIBRARY_PATH\nexec /usr/bin/htop "$@"\n' > /usr/local/bin/htop && chmod 755 /usr/local/bin/htop
COPY runcontrol/ITS3RunControl.patch runcontrol/ITS3start.patch /tmp/patches/
RUN cp /opt/eudaq-src/user/ITS3/python/ITS3RunControl.py /opt/eudaq-src/user/ITS3/python/ITS3RunControl.original.py && cd /opt/eudaq-src && git apply /tmp/patches/ITS3RunControl.patch && git apply /tmp/patches/ITS3start.patch
COPY runcontrol/hook.py /opt/eudaq-src/user/ITS3/python/hook.py
COPY msensor /opt/public-work/msensor
ENV PYTHONPATH=/opt/public-work:/opt/eudaq/lib:/opt/eudaq-src/user/ITS3/python
