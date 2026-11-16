# SPDX-License-Identifier: LGPL-3.0-or-later
option(USER_msensor_BUILD "build public synthetic module" OFF)
if(USER_msensor_BUILD)
  add_library(eudaq_module_msensor SHARED src/Converter.cc)
  target_link_libraries(eudaq_module_msensor PUBLIC ${EUDAQ_CORE_LIBRARY})
  target_include_directories(eudaq_module_msensor PRIVATE /opt/conda/include)
  install(TARGETS eudaq_module_msensor LIBRARY DESTINATION lib)
endif()
