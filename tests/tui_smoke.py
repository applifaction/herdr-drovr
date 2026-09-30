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
parser.add_argument('--mouse-name', action='store_true')
parser.add_argument('--close-workspace', action='store_true')
parser.add_argument('--theme', help='Herdr theme used by the isolated server and client')
parser.add_argument('--host-foreground', default='cdd6f4', help='Emulated outer terminal default text RGB')
parser.add_argument('--host-background', default='1e1e2e', help='Emulated outer terminal default background RGB')
args = parser.parse_args()
assert not (args.cancel_name or args.blank_name) or args.destination == 'new'
herdr = shutil.which('herdr')
assert herdr
results = {'host_colors': {'fg': args.host_foreground, 'bg': args.host_background}}
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
    if args.theme:
        with (root/'config.toml').open('a') as config:
            config.write(f'\n[theme]\nname = {json.dumps(args.theme)}\nauto_switch = false\n')

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

    def click_name_action(word):
        row = next(i for i, line in enumerate(screen.display) if '^c clear' in line and 'esc cancel' in line)
        col = screen.display[row].index(word) + 2
        keys(f'\x1b[<0;{col};{row+1}M\x1b[<0;{col};{row+1}m'.encode())

    def modal_geometry(lines):
        title_row = next(i for i, line in enumerate(lines) if 'rename tab' in line or 'new workspace' in line)
        # Split-pane frames also have corners: use the frame nearest the title.
        top = next(i for i in range(title_row-1, -1, -1) if '┌' in lines[i] and '┐' in lines[i])
        left = lines[top].index('┌')
        right = lines[top].index('┐', left)
        bottom = next(i for i in range(top+1, len(lines)) if lines[i][left] == '└' and lines[i][right] == '┘')
        return {'width': right-left+1, 'height': bottom-top+1}

    def text_colors(text):
        row = next(i for i, line in enumerate(screen.display) if text in line)
        col = screen.display[row].index(text)
        cell = screen.buffer[row][col]
        return {'fg': cell.fg, 'bg': cell.bg, 'reverse': cell.reverse, 'bold': cell.bold}

    def contrast(colors):
        # Measure the rendered text, not just whether it exists in a snapshot.
        def luminance(rgb):
            assert len(rgb) == 6, f'Expected rendered RGB, got {rgb!r}'
            srgb = [int(rgb[i:i+2], 16)/255 for i in (0, 2, 4)]
            linear = [v/12.92 if v <= .04045 else ((v+.055)/1.055)**2.4 for v in srgb]
            return sum(c*w for c, w in zip(linear, (.2126, .7152, .0722)))
        # pyte leaves SGR default colors unresolved. Resolve against the
        # explicit outer terminal fixture, independently of Herdr's UI theme.
        fg = args.host_foreground if colors['fg'] == 'default' else colors['fg']
        bg = args.host_background if colors['bg'] == 'default' else colors['bg']
        low, high = sorted([luminance(fg), luminance(bg)])
        return (high+.05)/(low+.05)

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
            if args.destination == 'new':
                keys(b'\x02T')  # native tab-name modal, for visual comparison
                wait_for(lambda: 'rename tab' in '\n'.join(screen.display), 'native name modal did not open')
                results['native_name_prompt'] = screen.display
                native_title_row = next(i for i, line in enumerate(screen.display) if 'rename tab' in line)
                native_input_row = native_title_row + 2
                native_input_col = screen.display[native_input_row].index('MOVED-TAB')
                native_input_cell = screen.buffer[native_input_row][native_input_col]
                results['native_input_colors'] = {'fg': native_input_cell.fg, 'bg': native_input_cell.bg}
                results['native_title_colors'] = text_colors('rename tab')
                keys(b'\x1b')
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
                wait_for(lambda: 'new workspace' in '\n'.join(screen.display) and '^c clear' in '\n'.join(screen.display),
                         'compact name modal did not open')
                results['name_prompt'] = screen.display
                results['input_colors'] = text_colors('Unmatched query')
                results['input_contrast'] = contrast(results['input_colors'])
                assert results['input_contrast'] >= 4.5, f"Unreadable name input: {results['input_colors']} (contrast {results['input_contrast']:.2f}:1)"
                results['modal_text_colors'] = {text: text_colors(text) for text in ['new workspace', 'save', 'clear', 'cancel']}
                for text, colors in results['modal_text_colors'].items():
                    assert contrast(colors) >= 4.5, f'Unreadable modal text {text!r}: {colors}'
                results['modal_geometry'] = modal_geometry(screen.display)
                assert results['modal_geometry'] == modal_geometry(results['native_name_prompt']) == {'width': 56, 'height': 7}
                assert 'Create workspace' not in '\n'.join(screen.display)
                assert 'move tab to' not in '\n'.join(screen.display)
                if args.blank_name:
                    if args.mouse_name:
                        click_name_action('clear')
                    else:
                        keys(b'\x03')  # ctrl-c clears, it must not cancel the modal
                    keys(b'\r')
                    wait_for(lambda: 'cannot be empty' in '\n'.join(screen.display), 'empty name was not rejected')
                    results['validation_colors'] = text_colors('cannot be empty')
                    assert contrast(results['validation_colors']) >= 4.5
                    assert len(rpc('workspace.list')['workspaces']) == 2
                if args.cancel_name:
                    if args.mouse_name:
                        click_name_action('cancel')
                    else:
                        keys(b'\x1b')
                    pump(.5)
                    assert len(rpc('workspace.list')['workspaces']) == 2
                    assert any(t['tab_id'] == src_tab for t in rpc('tab.list')['tabs'])
                    assert rpc('pane.get', {'pane_id': src_pane})['pane']['tab_id'] == src_tab
                    results['cancelled_without_changes'] = True
                else:
                    keys('\x15My new workspace ä'.encode())
                    results['edited_input_colors'] = text_colors('My new workspace ä')
                    assert contrast(results['edited_input_colors']) >= 4.5
                    if args.mouse_name:
                        click_name_action('save')
                    else:
                        keys(b'\r')
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
