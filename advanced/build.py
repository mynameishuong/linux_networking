"""Build the portable C core with cc/clang/GCC, or a supplied Zig executable."""
import argparse
import os
from pathlib import Path
import shlex
import shutil
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]


def library_path():
    suffix = '.dll' if os.name == 'nt' else ('.dylib' if sys.platform == 'darwin' else '.so')
    return ROOT / 'build' / ('v3_core' + suffix)


def build(compiler=None, target=None):
    sources = [ROOT / 'advanced/core/ecu_core.c', ROOT / 'advanced/core/fan_hal.c']
    output = library_path() if not target else ROOT / 'build/v3_core-linux.so'
    output.parent.mkdir(exist_ok=True)
    if compiler:
        command = [compiler, 'cc'] if Path(compiler).stem == 'zig' else [compiler]
    elif os.environ.get('CC'):
        command = shlex.split(os.environ['CC'], posix=os.name != 'nt')
    elif shutil.which('cc'):
        command = [shutil.which('cc')]
    else:
        cached = ROOT.parent / '.tmp/image-lab-toolchain/ziglang/zig.exe'
        if not cached.exists():
            raise RuntimeError('Install a C compiler or pass --cc PATH_TO_ZIG_OR_CC')
        command = [str(cached), 'cc']
    if target:
        command += ['-target', target]
    command += ['-std=c11', '-O0', '-g', '-Wall', '-Wextra', '-Wpedantic', '-Werror',
                '-shared', '-fPIC', *(str(source) for source in sources), '-o', str(output)]
    subprocess.run(command, check=True)
    print(output)
    return output


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--cc')
    parser.add_argument('--target', help='Optional Zig target for cross-build checking')
    args = parser.parse_args()
    build(args.cc, args.target)
