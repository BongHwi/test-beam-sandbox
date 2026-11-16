ARG PUBLIC_BASE=local/public-stack:native
# SPDX-License-Identifier: LGPL-3.0-or-later
FROM ${PUBLIC_BASE}
COPY docker/base/update-index.sh /tmp/update-index.sh
RUN bash /tmp/update-index.sh && DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends tmux=3.0a-2ubuntu0.4 libevent-2.1-7=2.1.11-stable-1 libutempter0=1.1.6-4 htop=2.2.0-2build1 && rm -rf /var/lib/apt/lists/*
# The public launcher runs ${EUDAQ}/bin/euCliLogger, the source-tree install location; this image installs EUDAQ in /opt/eudaq.
RUN test -x /opt/eudaq/bin/euCliLogger && ln -s /opt/eudaq/bin /opt/eudaq-src/bin
# The public launcher runs plain htop; the image's library path selects conda's incompatible ncurses, so run Ubuntu's htop with its own libraries.
RUN printf '#!/bin/sh\nunset LD_LIBRARY_PATH\nexec /usr/bin/htop "$@"\n' > /usr/local/bin/htop && chmod 755 /usr/local/bin/htop
COPY runcontrol/ITS3RunControl.patch runcontrol/ITS3start.patch /tmp/patches/
RUN cp /opt/eudaq-src/user/ITS3/python/ITS3RunControl.py /opt/eudaq-src/user/ITS3/python/ITS3RunControl.original.py && cd /opt/eudaq-src && git apply /tmp/patches/ITS3RunControl.patch && git apply /tmp/patches/ITS3start.patch
COPY runcontrol/hook.py /opt/eudaq-src/user/ITS3/python/hook.py
COPY msensor /opt/public-work/msensor
COPY virtual_alpide /opt/public-work/virtual_alpide
COPY virtual_trigger /opt/public-work/virtual_trigger
COPY session_core /opt/public-work/session_core
COPY tracksim/src /opt/public-work/tracksim-src
COPY integration /opt/public-work/integration
COPY session_setup /opt/public-work/session_setup
COPY configs/beam-profiles /opt/public-work/configs/beam-profiles
COPY tests/e2e/integration_analysis.py /opt/public-work/session_setup/integration_analysis.py
COPY eudaq_module /opt/eudaq-src/user/msensor
COPY docker/base/build-user-module.sh /tmp/build-user-module.sh
RUN /tmp/build-user-module.sh msensor
RUN mkdir /opt/virtual-producer-cwd && cd /opt/virtual-producer-cwd && git init -q && git config user.name 'Public synthetic fixture' && git config user.email 'fixture@localhost' && printf 'Fixed public virtual-producer working tree\n' > README && git add README && GIT_AUTHOR_DATE='2000-01-01T00:00:00Z' GIT_COMMITTER_DATE='2000-01-01T00:00:00Z' git commit -qm 'fixed virtual producer working tree'
ENV PYTHONPATH=/opt/public-work/virtual_trigger:/opt/public-work/virtual_alpide:/opt/public-work:/opt/public-work/tracksim-src:/opt/eudaq/lib:/opt/eudaq-src/user/ITS3/python
WORKDIR /opt/virtual-producer-cwd
