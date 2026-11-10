"""Decode the tiny PNG fixture without overwriting an existing input file."""
import base64
import pathlib

root = pathlib.Path(__file__).resolve().parents[1]
destination = root / 'inputs/sample.png'
destination.parent.mkdir(exist_ok=True)
data = base64.b64decode((root / 'assets/sample.png.b64').read_bytes().strip(), validate=True)
with destination.open('xb') as output:
    output.write(data)
print(f'Created {destination} ({len(data)} bytes)')
