#!/bin/bash
# SPDX-License-Identifier: LGPL-3.0-or-later
PYTHONPATH=/opt/public-work${PYTHONPATH:+:$PYTHONPATH} exec /opt/conda/bin/python -m nestdaq_mode.control prerun
