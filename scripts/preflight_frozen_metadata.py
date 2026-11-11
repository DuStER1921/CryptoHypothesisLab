"""Private, read-only metadata preflight (never opens market Parquet)."""
from __future__ import annotations

import argparse
from pathlib import Path
from hypolab.frozen import FrozenIndex, FrozenError


def main():
    parser=argparse.ArgumentParser(description='Read-only offline frozen metadata preflight')
    parser.add_argument('--metadata-root',type=Path,required=True)
    parser.add_argument('--universe-sha256',required=True)
    parser.add_argument('--calendar-sha256',required=True)
    args=parser.parse_args()
    idx=FrozenIndex.load(args.metadata_root,args.universe_sha256,args.calendar_sha256)
    # Only counts and schema status, never source filenames, rows or private paths.
    counts={family:sum(x[0]==family for x in idx.partitions)
            for family in ('klines','aggTrades','bookDepth')}
    print('FROZEN_METADATA_PASS', 'family_partitions='+str(counts),
          'c3_pass_candidates='+str(len(idx.c3_pass_days)),
          'REAL_RESEARCH=NOT_AUTHORIZED')


if __name__=='__main__':main()
