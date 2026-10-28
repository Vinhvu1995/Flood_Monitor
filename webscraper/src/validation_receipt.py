"""Validation receipt (src/validation_receipt.py) — SHA-256 gate."""
import hashlib, json, os, time

VERSION = 1

def _sha(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()

def write_receipt(run_dir: str, verdicts_file: str, verdicts: list,
                  raw_pairs: int, covered: int, dropped_explicit: int,
                  dropped_uncovered: int, kept: int, keep_uncovered: bool = False):
    vf = os.path.join(run_dir, "Vectorised_Files")
    hashes = {}
    if os.path.isdir(vf):
        for name in sorted(os.listdir(vf)):
            p = os.path.join(vf, name)
            if os.path.isfile(p):
                hashes[name] = _sha(p)
    receipt = {
        "version": VERSION, "applied_at": int(time.time()),
        "verdicts_file": os.path.basename(verdicts_file),
        "verdict_count": len(verdicts), "raw_pairs": raw_pairs,
        "covered_pairs": covered, "dropped_explicit": dropped_explicit,
        "dropped_uncovered": dropped_uncovered, "kept_pairs": kept,
        "keep_uncovered_override": bool(keep_uncovered),
        "vector_hashes": hashes,
    }
    out = os.path.join(run_dir, "Logs", "validation_receipt.json")
    os.makedirs(os.path.dirname(out), exist_ok=True)
    with open(out, "w") as f:
        json.dump(receipt, f, indent=2)
    return out

def check_receipt(run_dir: str, allow_override: bool = False):
    """Returns (ok, reason). Fails closed per 06 spec."""
    path = os.path.join(run_dir, "Logs", "validation_receipt.json")
    if not os.path.exists(path):
        return False, "no receipt"
    with open(path) as f:
        r = json.load(f)
    if r.get("version") != VERSION:
        return False, "wrong version"
    if r.get("verdict_count", 0) == 0 and r.get("raw_pairs", 1):
        return False, "records but zero verdicts"
    if r.get("keep_uncovered_override") and not allow_override:
        return False, "keep-uncovered without override"
    vf = os.path.join(run_dir, "Vectorised_Files")
    for name, want in (r.get("vector_hashes") or {}).items():
        p = os.path.join(vf, name)
        if not os.path.exists(p):
            return False, f"missing {name}"
        if _sha(p) != want:
            return False, f"hash changed {name}"
    return True, "ok"
