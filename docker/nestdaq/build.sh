#!/usr/bin/env bash
# SPDX-License-Identifier: LGPL-3.0-or-later
set -Eeuo pipefail
export PATH="/opt/build/tools/bin:/opt/nestdaq/bin:/opt/conda/bin:$PATH"
export LD_LIBRARY_PATH="/opt/nestdaq/lib:/opt/conda/lib:${LD_LIBRARY_PATH:-}"
export CMAKE_BUILD_PARALLEL_LEVEL=4
export GIT_TRACE=/opt/build-logs/git-trace.log PIP_INDEX_URL=https://pypi.org/simple PIP_VERBOSE=1
mkdir -p /opt/build/{src,work} /opt/nestdaq
checkout() {
  local url=$1 dir=$2 commit=$3
  git clone "$url" "$dir"
  git -C "$dir" checkout --detach "$commit"
  test "$(git -C "$dir" rev-parse HEAD)" = "$commit"
  git -C "$dir" submodule update --init --recursive
}
case "${1:-}" in
 tools)
  python -m venv /opt/build/tools
  case "${TARGETARCH:?}" in
   arm64) hash=42d9883b8958da285d53d5f69d40d9650c2d1bcf922d82b3ebdceb2b3a7d4521 ;;
   amd64) hash=1c8b05df0602365da91ee6a3336fe57525b137706c4ab5675498f662ae1dbcec ;;
   *) exit 2 ;;
  esac
  echo "cmake==3.31.6 --hash=sha256:$hash" > /tmp/cmake.requirement
  /opt/build/tools/bin/pip install --require-hashes -r /tmp/cmake.requirement
  rmdir /opt/build/tools/include/python3.11
  ln -s /opt/conda/include/python3.11 /opt/build/tools/include/python3.11 ;;
 aptdeps)
  bash /opt/build/update-index.sh
  DEBIAN_FRONTEND=noninteractive xargs -r -a /opt/build/config/apt-build.lock apt-get install -y --no-install-recommends
  rm -rf /var/lib/apt/lists/* ;;
 sources)
  checkout https://github.com/spadi-alliance/nestdaq.git /opt/build/src/nestdaq eda728718362cc0ee169bea9b111ffc3fda7ca6b
  checkout https://github.com/spadi-alliance/nestdaq-user-impl.git /opt/build/src/nestdaq-user-impl 3dd6b69f9b3c37eaa6b97f11c11d0b659c5ab064
  checkout https://github.com/slowproj/slowdash.git /opt/slowdash dd1138e91857d0065aba05b79d6510d6d6e9fa65
  checkout https://github.com/redis/hiredis.git /opt/build/src/hiredis ccad7ebaf99310957004661d1c5f82d2a33ebd10
  checkout https://github.com/redis/redis.git /opt/build/src/redis a176d1225ef66b6bd963231d77d5819909782329
  checkout https://github.com/RedisTimeSeries/RedisTimeSeries.git /opt/build/src/timeseries 05fd355db748676861dc4c17d19c8c1ca74c0154 ;;
 hiredis)
  cmake -S /opt/build/src/hiredis -B /opt/build/work/hiredis -DCMAKE_INSTALL_PREFIX=/opt/nestdaq -DCMAKE_BUILD_TYPE=Release -DCMAKE_C_COMPILER=/opt/conda/bin/cc -DDISABLE_TESTS=ON
  cmake --build /opt/build/work/hiredis --parallel 4
  cmake --install /opt/build/work/hiredis ;;
 external)
  cmake -S /opt/build/src/nestdaq/cmake -B /opt/build/work/external -DCMAKE_INSTALL_PREFIX=/opt/nestdaq -DCMAKE_BUILD_TYPE=RelWithDebInfo -DCMAKE_C_COMPILER=/opt/conda/bin/cc -DCMAKE_CXX_COMPILER=/opt/conda/bin/c++ -DBUILD_PARALLEL_LEVEL=4 -DWITH_OTEL_CPP=OFF -DCMAKE_DISABLE_FIND_PACKAGE_ZeroMQ=TRUE -DCMAKE_PREFIX_PATH=/opt/nestdaq -DCMAKE_PROJECT_nestdaq_external_dependencies_INCLUDE=/opt/build/config/preload.cmake
  cmake --build /opt/build/work/external --target ZeroMQ boost FairLogger FairMQ redis_plus_plus --parallel 1
  for pair in 'ZeroMQ 622fc6dde99ee172ebaa9c8628d85a7a1995a21d' 'boost ab7968a0bbcf574a7859240d1d8443f58ed6f6cf' 'FairLogger 71aac1199aa884da9430cd25c57353405249f0ae' 'FairMQ dcea48fcee9217992b0f304ab1691f9996ce60e3' 'redis_plus_plus a63ac43bf192772910b52e27cd2b42a6098a0071'; do
    read -r name commit <<< "$pair"
    test "$(git -C "/opt/build/work/external/$name-prefix/src/$name" rev-parse HEAD)" = "$commit"
  done ;;
 boost_iostreams)
  cd /opt/build/work/external/boost-prefix/src/boost
  export ZLIB_INCLUDE=/opt/conda/include ZLIB_LIBRARY_PATH=/opt/conda/lib BZIP2_INCLUDE=/opt/conda/include BZIP2_LIBRARY_PATH=/opt/conda/lib LZMA_INCLUDE=/opt/conda/include LZMA_LIBRARY_PATH=/opt/conda/lib
  ./b2 --with-iostreams --prefix=/opt/nestdaq --build-dir=/opt/build/work/boost-iostreams -j4 cxxstd=17 link=shared,static threading=multi variant=release cxxflags=-I/opt/conda/include linkflags=-L/opt/conda/lib install ;;
 redis)
  make -C /opt/build/src/redis -j4 BUILD_WITH_MODULES=no BUILD_TLS=no MALLOC=libc
  make -C /opt/build/src/redis install PREFIX=/opt/nestdaq BUILD_WITH_MODULES=no BUILD_TLS=no MALLOC=libc ;;
 timeseries)
  export CFLAGS='-I/opt/conda/include' CPPFLAGS='-I/opt/conda/include' LDFLAGS='-L/opt/conda/lib -Wl,-rpath,/opt/conda/lib'
  export openssl_prefix=/opt/conda C_INCLUDE_PATH=/opt/conda/include CPLUS_INCLUDE_PATH=/opt/conda/include LIBRARY_PATH=/opt/conda/lib
  make -C /opt/build/src/timeseries -j4 DEPS=1 SSL=1 VARIANT=nestdaq build
  module=$(find /opt/build/src/timeseries/bin -name redistimeseries.so -print -quit)
  test -n "$module"
  mkdir -p /opt/nestdaq/lib/redis/modules
  cp "$module" /opt/nestdaq/lib/redis/modules/redistimeseries.so ;;
 nestdaq)
  cmake -S /opt/build/src/nestdaq -B /opt/build/work/nestdaq -DCMAKE_INSTALL_PREFIX=/opt/nestdaq -DCMAKE_PREFIX_PATH=/opt/nestdaq -DCMAKE_BUILD_TYPE=RelWithDebInfo -DCMAKE_C_COMPILER=/opt/conda/bin/cc -DCMAKE_CXX_COMPILER=/opt/conda/bin/c++
  cmake --build /opt/build/work/nestdaq --parallel 4
  cmake --install /opt/build/work/nestdaq ;;
 filesink)
  cmake -S /opt/build/config/filesink -B /opt/build/work/filesink -DCMAKE_PREFIX_PATH='/opt/nestdaq;/opt/conda' -DCMAKE_BUILD_TYPE=RelWithDebInfo -DCMAKE_CXX_COMPILER=/opt/conda/bin/c++
  cmake --build /opt/build/work/filesink --target FileSink --parallel 4
  cp /opt/build/work/filesink/FileSink /opt/nestdaq/bin/ ;;
 slowdash)
  cd /opt/slowdash
  PIP_CONSTRAINT=/opt/build/config/slowdash-requirements.lock make PIP_REQS='uvicorn==0.34.0 hypercorn==0.17.3 websockets==14.2 pyyaml==6.0.2 psutil==6.1.1 bcrypt==4.2.1' PIP_OPTIONAL_DBS='redis==5.2.1' PIP_OPTIONAL_MSGS='' PIP_OPTIONAL_DEVS='' PIP_OPTIONAL_DATA='' ;;
 licenses)
  mkdir -p /opt/nestdaq/licenses
  for item in 'nestdaq /opt/build/src/nestdaq/LICENSE' 'nestdaq-user-impl /opt/build/src/nestdaq-user-impl/LICENSE' 'slowdash /opt/slowdash/LICENSE' 'hiredis /opt/build/src/hiredis/COPYING' 'redis /opt/build/src/redis/LICENSE.txt' 'redistimeseries /opt/build/src/timeseries/LICENSE.txt' 'boost /opt/build/work/external/boost-prefix/src/boost/LICENSE_1_0.txt' 'zeromq /opt/build/work/external/ZeroMQ-prefix/src/ZeroMQ/LICENSE' 'fairlogger /opt/build/work/external/FairLogger-prefix/src/FairLogger/LICENSE' 'fairmq /opt/build/work/external/FairMQ-prefix/src/FairMQ/LICENSE' 'redis-plus-plus /opt/build/work/external/redis_plus_plus-prefix/src/redis_plus_plus/LICENSE'; do
    read -r name path <<< "$item"; cp "$path" "/opt/nestdaq/licenses/$name.LICENSE"
  done
  for pkg in libmagic-mgc libmagic1 file libsigsegv2 m4 autoconf autotools-dev automake libtool; do cp "/usr/share/doc/$pkg/copyright" "/opt/nestdaq/licenses/apt-$pkg.LICENSE"; done
  cp /opt/build/tools/lib/python3.11/site-packages/cmake/data/doc/cmake-3.31/Copyright.txt /opt/nestdaq/licenses/cmake.LICENSE
  cp /opt/build/config/versions.lock /opt/nestdaq/versions.lock ;;
 check)
  redis-server --bind 127.0.0.1 --port 16379 --save '' --appendonly no --loadmodule /opt/nestdaq/lib/redis/modules/redistimeseries.so >/tmp/redis-check.log 2>&1 & pid=$!
  trap 'kill "$pid" 2>/dev/null || true; cat /tmp/redis-check.log' EXIT
  ok=0; for i in {1..20}; do if redis-cli -p 16379 ping | grep -q PONG; then ok=1; break; fi; sleep 1; done
  test "$ok" = 1
  module=$(redis-cli --raw -p 16379 MODULE LIST); printf '%s\n' "$module"
  grep -qx timeseries <<< "$module"; grep -qx 80600 <<< "$module"
  redis-cli --raw -p 16379 TS.ADD probe '*' 42 | grep -Eq '^[0-9]+$'
  redis-cli --raw -p 16379 TS.RANGE probe - + | grep -qx 42 ;;
 *) echo "unknown build stage: ${1:-}" >&2; exit 2 ;;
esac
