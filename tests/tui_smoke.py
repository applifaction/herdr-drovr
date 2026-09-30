"""Real Herdr/fzf regression test in a disposable server + PTY (requires pyte)."""
import argparse
import fcntl
import json
import os
from pathlib import Path
import pty
import select
import shutil
import socket
import struct
import subprocess
import tempfile
import termios
import time

import pyte

parser = argparse.ArgumentParser()
parser.add_argument('--plugin', default=str(Path(__file__).resolve().parents[1]))
parser.add_argument('--output', required=True)
parser.add_argument('--mode', choices=['tab', 'pane'], default='tab')
parser.add_argument('--destination', choices=['existing', 'new'], default='existing')
parser.add_argument('--split', action='store_true')
parser.add_argument('--cancel-name', action='store_true')
parser.add_argument('--blank-name', action='store_true')
parser.add_argument('--close-workspace', action='store_true')
args = parser.parse_args()
assert not (args.cancel_name or args.blank_name) or args.destination == 'new'
herdr = shutil.which('herdr')
assert herdr
results = {}
with tempfile.TemporaryDirectory(prefix='drovr-tui-') as temp:
    root = Path(temp)
    env = {k: v for k, v in os.environ.items() if not k.startswith('HERDR_')}
    env.update(HOME=temp, XDG_CONFIG_HOME=temp+'/config', XDG_STATE_HOME=temp+'/state',
               XDG_DATA_HOME=temp+'/data', XDG_CACHE_HOME=temp+'/cache', XDG_RUNTIME_DIR=temp+'/runtime',
               HERDR_SOCKET_PATH=temp+'/api.sock', HERDR_CLIENT_SOCKET_PATH=temp+'/client.sock',
               HERDR_CONFIG_PATH=temp+'/config.toml', SHELL='/bin/sh', TERM='xterm-256color', LANG='C.UTF-8')
    (root/'runtime').mkdir(mode=0o700)
    (root/'config.toml').write_text('''onboarding = false
[update]
version_check = false
manifest_check = false
[ui.sound]
enabled = false
[[keys.command]]
key = "prefix+M"
type = "plugin_action"
command = "drovr.move-tab"
[[keys.command]]
key = "prefix+m"
type = "plugin_action"
command = "drovr.move-pane"
''')

    def rpc(method, params=None):
        with socket.socket(socket.AF_UNIX) as sock:
            sock.settimeout(4)
            sock.connect(env['HERDR_SOCKET_PATH'])
            sock.sendall((json.dumps({'id': 'drovr-test', 'method': method, 'params': params or {}})+'\n').encode())
            with sock.makefile('rb') as stream:
                response = json.loads(stream.readline())
            if 'error' in response:
                raise RuntimeError(response['error'])
            return response['result']

    master, slave = pty.openpty()
    fcntl.ioctl(slave, termios.TIOCSWINSZ, struct.pack('HHHH', 42, 120, 0, 0))

    class TestScreen(pyte.Screen):
        def report_device_status(self, mode, **kwargs):
            return super().report_device_status(mode)

        def write_process_input(self, data):
            os.write(master, data.encode())

    screen = TestScreen(120, 42)
    stream = pyte.ByteStream(screen)
    server = client = None
    raw = bytearray()

    def pump(seconds=.4):
        until = time.monotonic() + seconds
        while time.monotonic() < until:
            readable, _, _ = select.select([master], [], [], min(.05, max(0, until-time.monotonic())))
            if readable:
                try:
                    data = os.read(master, 65536)
                except OSError:
                    break
                if not data:
                    break
                raw.extend(data)
                stream.feed(data)
        return '\n'.join(screen.display)

    def keys(data):
        os.write(master, data)
        return pump()

    def wait_for(predicate, description, seconds=5):
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
            pump(.1)
            value = predicate()
            if value:
                return value
        raise AssertionError(description+'\n'+'\n'.join(screen.display))

    def stop_owned(process):
        if process is None:
            return
        try:
            process.wait(timeout=3)
        except subprocess.TimeoutExpired:
            process.terminate()
            try:
                process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=2)

    try:
        with (root/'server.log').open('wb') as log:
            server = subprocess.Popen([herdr, 'server'], env=env, stdout=log, stderr=log)
            deadline = time.monotonic()+5
            while not (root/'api.sock').exists():
                if server.poll() is not None or time.monotonic() > deadline:
                    raise RuntimeError((root/'server.log').read_text()[-3000:])
                time.sleep(.05)
            subprocess.run([herdr, 'plugin', 'link', str(Path(args.plugin).resolve())], env=env,
                           capture_output=True, text=True, timeout=8, check=True)
            src = rpc('workspace.create', {'cwd': temp, 'label': 'SOURCE', 'focus': True})
            src_pane = src['root_pane']['pane_id']
            src_tab = src['tab']['tab_id']
            src_ws = src['workspace']['workspace_id']
            rpc('tab.rename', {'tab_id': src_tab, 'label': 'MOVED-TAB'})
            source_panes = [src_pane]
            if args.split:
                right = rpc('pane.split', {'target_pane_id': src_pane, 'direction': 'right', 'ratio': .6, 'focus': False})
                right_id = right['pane']['pane_id']
                bottom = rpc('pane.split', {'target_pane_id': right_id, 'direction': 'down', 'ratio': .5, 'focus': False})
                source_panes += [right_id, bottom['pane']['pane_id']]
            # Normally keep the source workspace alive; also test automatic closure.
            if not args.close_workspace:
                rpc('tab.create', {'workspace_id': src_ws, 'cwd': temp, 'label': 'STAY', 'focus': False})
            dst = rpc('workspace.create', {'cwd': temp, 'label': 'TARGET', 'focus': False})
            rpc('tab.rename', {'tab_id': dst['tab']['tab_id'], 'label': 'EXISTING-TARGET-TAB'})
            rpc('pane.focus', {'pane_id': src_pane})
            client = subprocess.Popen([herdr], env=env, stdin=slave, stdout=slave, stderr=slave, start_new_session=True)
            pump(1)
            keys(b'\x1b[I')
            keys(b'\x02M' if args.mode == 'tab' else b'\x02m')
            wait_for(lambda: f'move {args.mode} to' in '\n'.join(screen.display), 'picker did not open')
            if args.mode == 'pane' and args.destination == 'existing':
                keys(b'\x14')  # ctrl-t: show other workspaces
            if args.destination == 'new':
                keys(b'Unmatched query')
                if args.mode == 'pane':
                    keys(b'\x0e')  # ctrl-n: skip new-tab row
            else:
                keys(b'TARGET')
            keys(b'\r')
            if args.destination == 'new':
                wait_for(lambda: 'workspace name' in '\n'.join(screen.display), 'name prompt did not open')
                results['name_prompt'] = screen.display
                if args.blank_name:
                    keys(b'\x15\r')
                    wait_for(lambda: 'cannot be empty' in '\n'.join(screen.display), 'empty name was not rejected')
                    assert len(rpc('workspace.list')['workspaces']) == 2
                if args.cancel_name:
                    keys(b'\x1b')
                    pump(.5)
                    assert len(rpc('workspace.list')['workspaces']) == 2
                    assert any(t['tab_id'] == src_tab for t in rpc('tab.list')['tabs'])
                    assert rpc('pane.get', {'pane_id': src_pane})['pane']['tab_id'] == src_tab
                    results['cancelled_without_changes'] = True
                else:
                    keys('\x15My new workspace ä\r'.encode())
            if not args.cancel_name:
                def destination_workspace():
                    label = 'My new workspace ä' if args.destination == 'new' else 'TARGET'
                    return next((w for w in rpc('workspace.list')['workspaces'] if w.get('label') == label), None)
                dest_ws = wait_for(destination_workspace, 'destination workspace was not created')
                def moved_panes():
                    return [p for p in rpc('pane.list')['panes'] if p['workspace_id'] == dest_ws['workspace_id']]
                expected_count = (len(source_panes) if args.mode == 'tab' else 1) + (1 if args.destination == 'existing' else 0)
                panes = wait_for(lambda: moved_panes() if len(moved_panes()) == expected_count else None,
                                 'not all source panes were moved')
                if args.mode == 'tab':
                    moved = next(t for t in rpc('tab.list')['tabs'] if t.get('label') == 'MOVED-TAB'
                                 and t['workspace_id'] == dest_ws['workspace_id'])
                    layout = rpc('pane.layout', {'pane_id': next(p['pane_id'] for p in panes if p['tab_id'] == moved['tab_id'])})['layout']
                    assert len(layout['panes']) == len(source_panes)
                    if args.split:
                        assert sorted((s['direction'], s['ratio']) for s in layout['splits']) == [('down', .5), ('right', .6)]
                    results['layout'] = layout
                pump(.5)
                results['after_move'] = screen.display
                # Actual UI input, not server snapshot focus: shells retain birth pane IDs.
                marker = root/'focused-pane'
                keys(f'printf %s "$HERDR_PANE_ID" > {marker}\r'.encode())
                wait_for(marker.exists, 'terminal input did not reach any shell')
                actual = marker.read_text()
                expected = source_panes if args.mode == 'tab' else [src_pane]
                results['focus'] = {'expected_shells': expected, 'actual_shell': actual}
                assert actual in expected, f'UI focus stayed on {actual}; moved shells are {expected}'
            results['passed'] = True
    finally:
        if server is not None:
            try:
                rpc('server.stop')
            except (OSError, ValueError, RuntimeError):
                pass
        stop_owned(server)
        stop_owned(client)
        os.close(master)
        os.close(slave)
        output = Path(args.output)
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(results, ensure_ascii=False, indent=2)+'\n')
        Path(str(output)+'.ansi').write_bytes(raw)
print('PASS: real Herdr/fzf move and client keyboard focus')
