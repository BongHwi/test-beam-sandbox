# SPDX-License-Identifier: LGPL-3.0-or-later
option(USER_smoketest_BUILD "smoke module" OFF)
if(USER_smoketest_BUILD)
  add_library(eudaq_module_smoketest SHARED smoketest.cc)
  target_link_libraries(eudaq_module_smoketest PUBLIC ${EUDAQ_CORE_LIBRARY})
  install(TARGETS eudaq_module_smoketest LIBRARY DESTINATION lib)
endif()
