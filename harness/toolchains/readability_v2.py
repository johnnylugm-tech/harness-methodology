import sys
import subprocess
import json

def main():
    if len(sys.argv) < 2:
        print("Usage: python3 readability_v2.py <root_dir>", file=sys.stderr)
        sys.exit(1)

    root_dir = sys.argv[1]

    # 1. Run radon cc -a -j to get complexities
    cc_result = subprocess.run(
        ["radon", "cc", "-a", "-j", root_dir],
        capture_output=True,
        text=True
    )
    if cc_result.returncode != 0 and not cc_result.stdout.strip():
        # radon cc returns non-zero if issues found, but we just want JSON
        pass
    
    try:
        cc_data = json.loads(cc_result.stdout)
    except json.JSONDecodeError:
        print(f"Error parsing radon cc output: {cc_result.stdout}", file=sys.stderr)
        sys.exit(1)

    # 2. Run radon raw -j to get LLOC
    raw_result = subprocess.run(
        ["radon", "raw", "-j", root_dir],
        capture_output=True,
        text=True
    )
    try:
        raw_data = json.loads(raw_result.stdout)
    except json.JSONDecodeError:
        print(f"Error parsing radon raw output: {raw_result.stdout}", file=sys.stderr)
        sys.exit(1)

    # 3. Calculate LLOC-weighted CC score
    total_weighted_cc = 0.0
    total_lloc = 0

    for file_path, blocks in cc_data.items():
        if not isinstance(blocks, list):
            continue
        
        # Calculate file average CC
        complexities = [b.get("complexity", 1) for b in blocks if isinstance(b, dict) and "complexity" in b]
        if not complexities:
            file_avg_cc = 1.0
        else:
            file_avg_cc = sum(complexities) / len(complexities)

        # Get file LLOC
        file_raw = raw_data.get(file_path, {})
        if not isinstance(file_raw, dict):
            continue
            
        file_lloc = file_raw.get("lloc", 0)
        
        # We only consider files that have some LLOC
        if file_lloc > 0:
            total_weighted_cc += file_avg_cc * file_lloc
            total_lloc += file_lloc

    if total_lloc == 0:
        # No analysable files
        print(json.dumps({}))
        sys.exit(0)

    # Final project average CC
    project_avg_cc = total_weighted_cc / total_lloc

    # Convert to 0-100 score: CC=1 -> 100, CC=20 -> 0
    # Formula: 100 - (cc - 1) * (100 / 19)
    score = 100.0 - (project_avg_cc - 1.0) * (100.0 / 19.0)
    score = max(0.0, min(100.0, score))

    # Output in a format tool_runners.py can easily parse or just our custom format
    out = {
        "project_score": round(score, 1),
        "project_avg_cc": round(project_avg_cc, 2),
        "total_lloc": total_lloc
    }
    
    # Let's also output file-level scores in the format similar to radon mi
    # so _score_readability_v2 can just sum it if we want, but it's better to just read project_score.
    print(json.dumps(out, indent=2))

if __name__ == "__main__":
    main()
