"""
Utility script to clone/copy a Position in SAP SuccessFactors Employee Central.
Powered by PositionService and MetadataEngine.

Usage:
    python copy_position.py --source 3000803 --target 3000803_COPY
"""

import argparse
import json
import sys
from sf_client import SFClient
from position_service import PositionService


def copy_position(source_code: str, target_code: str, title_suffix: str = "(Copy)"):
    client = SFClient()
    service = PositionService(client)

    print(f"Fetching source position '{source_code}' ...")
    res = service.clone_position(source_code=source_code, target_code=target_code, title_suffix=title_suffix)
    print(f"\nSUCCESS: Position '{target_code}' created as a clone of '{source_code}'.")
    print(json.dumps(res, indent=2))
    return res


def main():
    parser = argparse.ArgumentParser(description="Clone an existing Position in SF Employee Central")
    parser.add_argument("--source", required=True, help="Source Position external code (e.g. 3000803)")
    parser.add_argument("--target", required=True, help="Target Position external code (e.g. 3000803_COPY)")
    parser.add_argument("--suffix", default="(Copy)", help="Title suffix (default: '(Copy)')")
    args = parser.parse_args()

    copy_position(source_code=args.source, target_code=args.target, title_suffix=args.suffix)


if __name__ == "__main__":
    main()
