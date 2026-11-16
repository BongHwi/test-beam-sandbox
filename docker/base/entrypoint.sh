#!/bin/bash
# SPDX-License-Identifier: LGPL-3.0-or-later
# ROOT base images may contain an activation script; a pinned environment may not.
if [ -r /opt/conda/etc/profile.d/conda.sh ]; then
    . /opt/conda/etc/profile.d/conda.sh && conda activate base
fi
exec "$@"
