"""Show one message's receive/decode/control/feedback trace from a JSONL file."""
import argparse
import json
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('file', type=Path)
    parser.add_argument('--trace', type=int)
    parser.add_argument('--stage')
    args = parser.parse_args()
    with args.file.open(encoding='utf-8') as file:
        for line in file:
            item = json.loads(line)
            if args.trace is not None and item['trace'] != args.trace:
                continue
            if args.stage and item['stage'] != args.stage:
                continue
            timestamp, trace, stage = (item.pop(key) for key in ('t_ms', 'trace', 'stage'))
            item.pop('boot_id', None)
            print(f'{timestamp:>12}  trace={trace:<5} {stage:<18} {json.dumps(item)}')


if __name__ == '__main__':
    main()
