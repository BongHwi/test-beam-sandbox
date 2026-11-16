FROM local/runcontrol:native
# SPDX-License-Identifier: LGPL-3.0-or-later
COPY virtual_alpide /opt/public-work/virtual_alpide
ENV PYTHONPATH=/opt/public-work/virtual_alpide:/opt/public-work:/opt/eudaq/lib:/opt/eudaq-src/user/ITS3/python
RUN mkdir /opt/virtual-producer-cwd && cd /opt/virtual-producer-cwd && git init -q && git config user.name 'Public synthetic fixture' && git config user.email 'fixture@localhost' && printf 'Fixed public virtual-producer working tree\n' > README && git add README && GIT_AUTHOR_DATE='2000-01-01T00:00:00Z' GIT_COMMITTER_DATE='2000-01-01T00:00:00Z' git commit -qm 'fixed virtual producer working tree'
WORKDIR /opt/virtual-producer-cwd
