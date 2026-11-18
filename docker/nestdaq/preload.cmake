# SPDX-License-Identifier: LGPL-3.0-or-later
# The upstream redis++ external project depends on a hiredis target even when
# the pinned hiredis package is already installed and found by CMake.
add_custom_target(hiredis)
# Only the non-Redis targets are built by this external project. Prevent its
# configuration from fetching unused Redis module source trees.
file(MAKE_DIRECTORY /opt/build/empty)
set(FETCHCONTENT_SOURCE_DIR_REDIS_SRC /opt/build/src/redis CACHE PATH "" FORCE)
foreach(name REDISBLOOM_SRC REDISEARCH_SRC REDISJSON_SRC REDISTIMESERIES_SRC)
  set(FETCHCONTENT_SOURCE_DIR_${name} /opt/build/empty CACHE PATH "" FORCE)
endforeach()
