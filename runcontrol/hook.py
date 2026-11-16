# SPDX-License-Identifier: LGPL-3.0-or-later
"""Optional observation and control of the public ITS3 RunControl."""
import json
import datetime
import fcntl
import logging
import os
import queue
import re
import socket
import tempfile
import threading
import time
import uuid

LOG = logging.getLogger('its3-hook')
KEYS = {'S': 'stop_next', 'R': 'repeat', 'T': 'terminate'}

class Hook:
    def __init__(self, rc, status_file=None, control_socket=None):
        self.rc, self.path, self.socket_path = rc, status_file, control_socket
        self.guard = threading.RLock()
        self.seq = 0
        self.history_seq = 0
        self.history_fd = None
        if self.path:
            try:
                history_path = os.path.join(os.path.dirname(os.path.abspath(self.path)), 'control-history.jsonl')
                self.history_fd = os.open(history_path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o644)
            except OSError:
                LOG.exception('control history unavailable')
        self.state = 'CONWAIT'
        self.last = None
        self.pending = None
        self._active = None
        self._stopped = False
        self.config = {'index': None, 'path': None, 'pending_paths': [], 'pass': None}
        self.target = None
        self.run_number = None
        self.configuration_bytes = None
        self.messages = {}
        self.source_limits = {}
        self.listener = None
        self.closed = False
        self.snapshot_request = threading.Event()
        if self.path:
            threading.Thread(target=self.snapshot_worker, daemon=True).start()
        self.sink_socket = os.environ.get('SESSION_SOURCE_SOCKET')
        self.sink_epoch = None
        self.sink_initialized = set()
        self.sink_queue = queue.Queue()
        if self.sink_socket:
            threading.Thread(target=self.send_sink_progress, daemon=True).start()
        self.install()
        if control_socket:
            try:
                self.listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
                self.listener.bind(control_socket)
                os.chmod(control_socket, 0o600)
                self.socket_identity = os.stat(control_socket).st_ino
                self.listener.listen(8)
                self.listener.settimeout(.2)
                threading.Thread(target=self.serve, daemon=True).start()
            except OSError:
                LOG.exception('control socket unavailable')
                if self.listener: self.listener.close(); self.listener = None
        self.snapshot()

    def install(self):
        rc = self.rc
        if hasattr(rc, 'ReadConfigureFile'):
            original_read = rc.ReadConfigureFile
            def read_configuration(path):
                with open(str(path) + '.lock', 'a+') as lock:
                    fcntl.flock(lock, fcntl.LOCK_EX)
                    with open(path, 'rb') as source:
                        content = source.read()
                    result = original_read(path)
                    with self.guard:
                        self.configuration_bytes = content
                    return result
            rc.ReadConfigureFile = read_configuration
        if self.sink_socket:
            original_start = rc.StartRun
            def start_run(*args, **kwargs):
                with self.guard:
                    self.sink_epoch = uuid.uuid4().hex
                    self.sink_initialized = set()
                    message = {'op':'sink_begin', 'epoch':self.sink_epoch,
                               'sinks':list(self.rc.collectors)}
                deadline = time.monotonic() + 10
                while not self.send_sink_message(message):
                    if time.monotonic() >= deadline:
                        raise RuntimeError('session sink barrier unavailable before StartRun')
                    time.sleep(.05)
                return original_start(*args, **kwargs)
            rc.StartRun = start_run
        tui = rc.tui
        original_key = tui.handle_keys
        def key(value):
            # Keep the public TUI's key semantics, including keys pressed between runs.
            original_key(value)
            if value in KEYS:
                with self.guard:
                    self.last = {'id':uuid.uuid4().hex, 'kind':KEYS[value],
                                 'source':'keyboard', 'seq':self.seq+1}
                    self.history()
                self.snapshot_request.set()
        tui.handle_keys = key
        original_state = tui.set_state
        def state(value):
            original_state(value)
            if value == 'RUNNING':
                self.read_run_number()
            with self.rc.lock:
                with self.guard:
                    self.state = value
                    if value == 'STOPPED':
                        self._stopped = True
                        if self._active == 'stop_next':
                            self._active = self.pending = None
                    elif value == 'TERMINATED':
                        self._active = self.pending = None
            self.snapshot()
        tui.set_state = state
        original_config = tui.update_configs_list
        def config(paths, iteration):
            original_config(paths, iteration)
            with self.rc.lock:
                with self.guard:
                    self.config = {'index': len(rc.configs)-len(paths), 'path': paths[0] if paths else None, 'pending_paths': list(paths), 'pass': iteration}
                    if self._stopped and self._active == 'repeat':
                        self._active = self.pending = None
                    self._stopped = False
            self.snapshot()
        tui.update_configs_list = config
        original_target = tui.target_progress_run
        def target(value):
            original_target(value)
            with self.guard: self.target = value
            self.snapshot()
        tui.target_progress_run = target
        # Pybind virtual callbacks dispatch through the Python class, so wrap its methods.
        cls = type(rc)
        for method in ('DoConnect', 'DoDisconnect', 'DoStatus'):
            original = getattr(cls, method)
            def callback(instance, *args, _original=original, _method=method):
                result = _original(instance, *args)
                hook = getattr(instance, '_its3_hook', None)
                if hook:
                    if _method == 'DoStatus':
                        try:
                            with hook.guard:
                                hook.messages[args[0].GetName()] = args[1].GetMessage()
                                hook.source_limits[args[0].GetName()] = args[1].GetTag('source_limit', '')
                            hook.observe_sink(args[0], args[1])
                        except Exception: LOG.exception('status message')
                    hook.snapshot()
                return result
            setattr(cls, method, callback)
        rc._its3_hook = self

    def snapshot_worker(self):
        while not self.closed:
            if self.snapshot_request.wait(0.2):
                self.snapshot_request.clear()
                self.snapshot()

    def raw_run_number(self):
        # The public RunControl does not give its run number to Python; the data collector names the
        # raw file of each run after it (run<number>.raw) in the session directory of the status file.
        if not self.path: return None
        try: names = os.listdir(os.path.dirname(os.path.abspath(self.path)))
        except OSError: return None
        numbers = [int(m.group(1)) for m in (re.fullmatch(r'run0*(\d+)\.raw', n) for n in names) if m]
        return max(numbers) if numbers else None

    def read_run_number(self):
        # Called when a run reaches RUNNING: its raw file is open by then; wait briefly if it is not yet listed.
        with self.guard: content = self.configuration_bytes
        number = self.raw_run_number()
        if number is not None and number != self.run_number:
            with self.guard: self.run_number = number
            self.keep_configuration(number, content)
            return
        def later():
            deadline = time.monotonic() + 10
            while time.monotonic() < deadline and not self.closed:
                time.sleep(.2)
                found = self.raw_run_number()
                if found is not None and found != self.run_number:
                    with self.guard: self.run_number = found
                    self.keep_configuration(found, content)
                    self.snapshot()
                    return
        threading.Thread(target=later, daemon=True).start()

    def keep_configuration(self, number, content):
        if not self.path: return
        folder = os.path.dirname(os.path.abspath(self.path))
        if content is None: return
        try:
            raw = next(n for n in os.listdir(folder) if re.fullmatch(r'run0*' + str(number) + r'\.raw', n))
            destination = os.path.join(folder, raw[:-4] + '.conf')
            fd, temporary = tempfile.mkstemp(prefix='.run-conf-', dir=folder)
            try:
                with os.fdopen(fd, 'wb') as out: out.write(content)
                with self.guard:
                    if not os.path.exists(destination): os.replace(temporary, destination)
            finally:
                if os.path.exists(temporary): os.unlink(temporary)
        except (OSError, StopIteration): LOG.exception('run configuration unavailable')

    def observe_sink(self, connection, status):
        if not self.sink_socket or connection.GetName() not in self.rc.collectors:
            return
        import pyeudaq
        if status.GetState() != pyeudaq.Status.STATE_RUNNING:
            return
        text = status.GetTag('DataEventN', '')
        if not text.isdecimal():
            return
        count = int(text); name = connection.GetName()
        with self.guard:
            if self.sink_epoch is None: return
            if count == 0: self.sink_initialized.add(name)
            message = {'op':'sink_progress', 'epoch':self.sink_epoch,
                       'sinks':list(self.rc.collectors), 'sink':name,
                       'events':count, 'initialized':name in self.sink_initialized}
        self.sink_queue.put(message)

    def send_sink_progress(self):
        # The RunControl callback must not block on the independent source.
        while not self.closed:
            try: message = self.sink_queue.get(timeout=.2)
            except queue.Empty: continue
            while not self.closed:
                with self.guard:
                    if message['epoch'] != self.sink_epoch: break
                if self.send_sink_message(message): break
                time.sleep(.05)

    def send_sink_message(self, message):
        try:
            with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
                client.settimeout(.5)
                client.connect(self.sink_socket)
                client.sendall((json.dumps(message, separators=(',', ':'))+'\n').encode())
                with client.makefile('rb') as stream: reply = json.loads(stream.readline())
                return reply.get('kind') == message['op']
        except (OSError, ValueError): return False

    def snapshot(self):
        if not self.path: return
        try:
            import pyeudaq
            names = {pyeudaq.Status.STATE_ERROR:'ERROR',pyeudaq.Status.STATE_UNINIT:'UNINIT',pyeudaq.Status.STATE_UNCONF:'UNCONF',pyeudaq.Status.STATE_CONF:'CONF',pyeudaq.Status.STATE_STOPPED:'STOPPED',pyeudaq.Status.STATE_RUNNING:'RUNNING'}
            with self.rc.lock:
                connections = []
                for name in sorted(set(self.rc.connections) | set(self.rc.status)):
                    role = 'producer' if name in self.rc.dataproducers+self.rc.moreproducers else 'collector' if name in self.rc.collectors else 'logger'
                    connections.append({'name':name,'role':role,'connected':name in self.rc.connections,'state':names.get(self.rc.status.get(name)), 'data_events':self.rc.ndev.get(name,0),'status_events':self.rc.nsev.get(name,0),'message':self.messages.get(name,''), 'source_limit':self.source_limits.get(name,'')})
                events = min((self.rc.ndev.get(n,0) for n in self.rc.dataproducers),default=0)
            with self.guard:
                self.seq += 1
                doc = {'schema':'its3-runcontrol-status/v1','seq':self.seq,'rc_state':self.state,'connections':connections,'config':dict(self.config),'run':{'number':self.run_number,'target_events':self.target,'events':events},'pending_intent':self.pending,'last_intent':self.last,'updated_monotonic_ns':time.monotonic_ns()}
                dirname = os.path.dirname(os.path.abspath(self.path))
                fd, temporary = tempfile.mkstemp(prefix='.its3-status-',dir=dirname)
                try:
                    with os.fdopen(fd,'w') as f: json.dump(doc,f); f.write('\n')
                    os.replace(temporary,self.path)
                finally:
                    if os.path.exists(temporary): os.unlink(temporary)
        except Exception: LOG.exception('status snapshot unavailable')

    def apply_after_start(self):
        # Called by RunControl under rc.lock after it sets running=True.
        if self._active is not None:
            self.rc.running = False
            if self._active == 'repeat': self.rc.repeat = True
            if self._active == 'terminate': self.rc.halt = True

    def intent(self, kind, source, ident):
        with self.rc.lock:
            with self.guard:
                if self.closed or self.state in ('STOPPED', 'TERMINATED', 'ERROR'):
                    return {'id': ident, 'accepted': False, 'reason': 'no applicable run'}
                if self._active is not None:
                    return {'id': ident, 'accepted': False, 'reason': 'another request is pending'}
                self._active = self.pending = kind
                self.last = {'id':ident,'kind':kind,'source':source,'seq':self.seq+1}
                self.history()
                if kind == 'terminate': self.rc.halt = True
                self.rc.running = False
                if kind == 'repeat': self.rc.repeat = True
        self.snapshot()
        with self.guard: return {'id':ident,'accepted':True,'seq':self.seq,'rc_state':self.state}

    def history(self):
        # Called under guard immediately after last is assigned.
        if self.history_fd is None: return
        row = {'format':1, 'seq':self.history_seq+1,
               'utc':datetime.datetime.now(datetime.timezone.utc).isoformat(),
               'monotonic_ns':time.monotonic_ns(), 'kind':self.last['kind'],
               'source':self.last['source'], 'id':self.last['id'],
               'status_seq':self.last['seq'], 'rc_state':self.state}
        try:
            os.write(self.history_fd, (json.dumps(row, separators=(',', ':'))+'\n').encode())
            self.history_seq += 1
        except OSError:
            LOG.exception('control history write failed')

    def serve(self):
        while not self.closed:
            try: connection,_ = self.listener.accept()
            except socket.timeout: continue
            except OSError: break
            threading.Thread(target=self.client,args=(connection,),daemon=True).start()

    def client(self, connection):
        with connection:
            connection.settimeout(10)
            try:
                data=b''
                while b'\n' not in data and len(data)<65536:
                    part=connection.recv(4096)
                    if not part: break
                    data+=part
                try:
                    request=json.loads(data.split(b'\n',1)[0]); ident=request.get('id')
                    if not isinstance(ident,str) or not ident or request.get('intent') not in KEYS.values(): raise ValueError('invalid request or intent')
                    response=self.intent(request['intent'],'socket',ident)
                except (ValueError,TypeError,AttributeError): response={'id':None,'accepted':False,'reason':'invalid request or intent'}
                connection.sendall((json.dumps(response)+'\n').encode())
            except (OSError, ValueError): LOG.exception('control client error')

    def close(self):
        self.closed=True
        with self.guard:
            if self.history_fd is not None:
                try: os.close(self.history_fd)
                except OSError: LOG.exception('control history close failed')
                self.history_fd = None
        if self.listener:
            self.listener.close()
            try:
                if os.stat(self.socket_path).st_ino == self.socket_identity: os.unlink(self.socket_path)
            except FileNotFoundError: pass
            except OSError: LOG.exception('socket cleanup failed')
