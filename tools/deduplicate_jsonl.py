#!/usr/bin/env python3
"""Deduplicate JSONL file based on event_id field.

Keeps only the first occurrence of each event_id in the file.
"""

import json
import sys
from pathlib import Path
from typing import Any


def deduplicate_jsonl(
    input_file: Path,
    output_file: Path | None = None,
    id_field: str = "event_id",
    in_place: bool = False,
    event_types_to_dedup: list[str] | None = None,
) -> tuple[int, int]:
    """Deduplicate JSONL file based on specified ID field.
    
    Args:
        input_file: Path to input JSONL file
        output_file: Path to output file (if None and not in_place, adds .deduped suffix)
        id_field: Field name to use for deduplication (default: "event_id")
        in_place: If True, overwrite input file (default: False)
        event_types_to_dedup: List of event types to deduplicate. If None, deduplicates all events.
                             If empty list, no deduplication is performed.
    
    Returns:
        Tuple of (total_lines, unique_lines)
    """
    if not input_file.exists():
        raise FileNotFoundError(f"Input file not found: {input_file}")
    
    # Determine output file
    if in_place:
        output_file = input_file
    elif output_file is None:
        output_file = input_file.with_suffix(".deduped.jsonl")
    
    seen_ids: set[str | int] = set()
    unique_lines: list[str] = []
    total_lines = 0
    duplicates = 0
    
    # Read and deduplicate
    print(f"Reading {input_file}...")
    with open(input_file, "r", encoding="utf-8") as f:
        for line_num, line in enumerate(f, 1):
            total_lines += 1
            
            if not line.strip():
                # Skip empty lines
                continue
            
            try:
                # Parse JSON from line
                obj: dict[str, Any] = json.loads(line)
                
                # Get event type
                event_type = obj.get("event_type", "")
                
                # Check if this event type should be deduplicated
                should_dedup = (
                    event_types_to_dedup is None  # Dedup all if not specified
                    or event_type in event_types_to_dedup  # Dedup if in the list
                )
                
                if not should_dedup:
                    # Keep all events that shouldn't be deduplicated
                    unique_lines.append(line.rstrip("\n"))
                    continue
                
                # Get ID field value for events that should be deduplicated
                event_id = obj.get(id_field)
                if event_id is None:
                    # Events without ID field are kept (can't deduplicate them)
                    unique_lines.append(line.rstrip("\n"))
                    continue
                
                # Check if we've seen this ID before (only for events we're deduplicating)
                if event_id not in seen_ids:
                    seen_ids.add(event_id)
                    unique_lines.append(line.rstrip("\n"))
                else:
                    duplicates += 1
                    
            except json.JSONDecodeError as e:
                print(f"Warning: Line {line_num} is not valid JSON: {e}, skipping", file=sys.stderr)
                continue
    
    # Write deduplicated output
    print(f"Writing {len(unique_lines)} unique lines to {output_file}...")
    with open(output_file, "w", encoding="utf-8") as f:
        for line in unique_lines:
            f.write(line + "\n")
    
    print(f"Done: {total_lines} total lines, {len(unique_lines)} unique, {duplicates} duplicates removed")
    
    return total_lines, len(unique_lines)


def main() -> None:
    """CLI entry point."""
    import argparse
    
    parser = argparse.ArgumentParser(
        description="Deduplicate JSONL file based on event_id field",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "input_file",
        type=Path,
        help="Input JSONL file to deduplicate",
    )
    parser.add_argument(
        "-o", "--output",
        type=Path,
        default=None,
        help="Output file path (default: input_file.deduped.jsonl)",
    )
    parser.add_argument(
        "-i", "--in-place",
        action="store_true",
        help="Overwrite input file with deduplicated version",
    )
    parser.add_argument(
        "--id-field",
        default="event_id",
        help="Field name to use for deduplication (default: event_id)",
    )
    parser.add_argument(
        "--event-types",
        nargs="+",
        default=None,
        help="Event types to deduplicate (default: all events). Example: --event-types in_game_critical",
    )
    
    args = parser.parse_args()
    
    try:
        total, unique = deduplicate_jsonl(
            input_file=args.input_file,
            output_file=args.output,
            id_field=args.id_field,
            in_place=args.in_place,
            event_types_to_dedup=args.event_types,
        )
        print(f"\nSummary: {unique}/{total} unique items ({100 * unique / total:.1f}%)")
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()

