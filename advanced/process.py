"""Launch a loopback ECU with ephemeral ports for demos and integration tests."""
import json
from pathlib import Path
import queue
import subprocess
import sys
import threading

ROOT = Path(__file__).resolve().parents[1]


class LabProcess:
    def __init__(self, **config):
        arguments = [sys.executable, '-m', 'advanced.ecu', '--control-stdin']
        for name in ('doip_port', 'signal_port', 'fan_port', 'telemetry_port', 'event_port',
                     'telemetry_event_port', 'sd_port'):
            config.setdefault(name, 0)
        for name, value in config.items():
            option = '--' + name.replace('_', '-')
            if value is True:
                arguments.append(option)
            elif value is not False and value is not None:
                arguments += [option, str(value)]
        self.process = subprocess.Popen(arguments, cwd=ROOT, stdin=subprocess.PIPE,
                                        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        ready = queue.Queue()
        self.reader = threading.Thread(target=lambda: ready.put(self.process.stdout.readline()), daemon=True)
        self.reader.start()
        try:
            line = ready.get(timeout=8)
            self.info = json.loads(line)
            if self.info.get('event') != 'READY':
                raise ValueError('Missing READY record')
        except (queue.Empty, ValueError) as error:
            self.process.terminate()
            _, stderr = self.process.communicate(timeout=5)
            raise RuntimeError(f'Cannot start virtual ECU: {stderr}') from error

    def close(self):
        if self.process.stdin:
            self.process.stdin.close()
            self.process.stdin = None
        try:
            stdout, stderr = self.process.communicate(timeout=5)
        except subprocess.TimeoutExpired:
            self.process.kill()
            self.process.communicate(timeout=3)
            raise RuntimeError('Virtual ECU failed to shut down')
        if self.process.returncode:
            raise RuntimeError(f'Virtual ECU exited {self.process.returncode}: {stderr}\n{stdout}')

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()
