#!/usr/bin/env bash
# SPDX-License-Identifier: LGPL-3.0-or-later
set -euo pipefail
source_dir=${1:-/tmp/integration}
output_dir=${2:-/tmp}
c++ -std=c++20 -shared -fPIC -O2 -I"$source_dir" -I/opt/corry-src/src \
    -I/opt/corry-src/3rdparty -I/opt/corry-src/3rdparty/GeneralBrokenLines/include -I/opt/eudaq/include \
    -I/opt/conda/include -I/usr/include/eigen3 \
    -DCORRYVRECKAN_MODULE_GLOBAL=1 -DCORRYVRECKAN_MODULE_NAME=EventDefinitionRawTrigger \
    -DCORRYVRECKAN_MODULE_HEADER='"EventDefinitionRawTrigger.h"' \
    "$source_dir/EventDefinitionRawTrigger.cpp" /opt/corry-src/src/core/module/dynamic_module_impl.cpp \
    -L/opt/corry/lib -L/opt/eudaq/lib -L/opt/conda/lib -lCorryvreckanCore \
    -lCorryvreckanObjects -lCorryvreckanUtilities -leudaq_core $(root-config --libs) \
    -o "$output_dir/libCorryvreckanModuleEventDefinitionRawTrigger.so"
c++ -std=c++20 -shared -fPIC -I/opt/eudaq/include "$source_dir/MSensorStatusConverter.cc" \
    -L/opt/eudaq/lib -leudaq_core -o "$output_dir/libmsensor_status.so"
