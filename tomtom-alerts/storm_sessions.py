"""Storm sessions (3h window, cap 12, 9000 km2) with NFS lock."""
import json, os, time, math

WINDOW_H = 3
CAP = 12
MAX_KM2 = 9000
LOCK_RETRIES = 3
LOCK_STALE_S = 1500

def iou(a, b):
    ax0, ay0, ax1, ay1 = a
    bx0, by0, bx1, by1 = b
    ix0, iy0, ix1, iy1 = max(ax0, bx0), max(ay0, by0), min(ax1, bx1), min(ay1, by1)
    if ix1 <= ix0 or iy1 <= iy0:
        return 0.0
    inter = (ix1 - ix0) * (iy1 - iy0)
    union = (ax1 - ax0) * (ay1 - ay0) + (bx1 - bx0) * (by1 - by0) - inter
    return inter / union if union else 0.0

def centroid(b):
    return ((b[0] + b[2]) / 2, (b[1] + b[3]) / 2)

def dist(a, b):
    ax, ay = centroid(a)
    bx, by = centroid(b)
    return math.hypot(ax - bx, ay - by) * 111.0  # deg->km approx

def acquire_lock(path):
    for _ in range(LOCK_RETRIES):
        try:
            fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            os.write(fd, str(os.getpid()).encode())
            os.close(fd)
            return True
        except FileExistsError:
            try:
                if time.time() - os.path.getmtime(path) > LOCK_STALE_S:
                    os.remove(path)
                    continue
            except FileNotFoundError:
                pass
            time.sleep(2)
    return False

def update_sessions(sessions, detections, now_ts):
    """Greedy IoU>=0.10 else centre<=25km; expire past window_end; cap 12."""
    now = now_ts
    for s in sessions:
        s.setdefault("history", [])
    used = set()
    for det in detections:
        best, best_key = None, None
        for i, s in enumerate(sessions):
            if i in used:
                continue
            v = iou(s["bbox"], det["bbox"])
            if v >= 0.10 and (best is None or v > best_key):
                best, best_key = i, v
        if best is None:
            for i, s in enumerate(sessions):
                if i in used:
                    continue
                if dist(s["bbox"], det["bbox"]) <= 25:
                    best = i
                    break
        if best is not None:
            s = sessions[best]
            s["history"] = (s.get("history", []) + [s["bbox"]])[-12:]
            s["bbox"] = det["bbox"]
            s["window_end"] = now + WINDOW_H * 3600
            used.add(best)
        else:
            sessions.append({"id": f"s_{now}_{len(sessions)}", "bbox": det["bbox"],
                             "window_end": now + WINDOW_H * 3600, "history": []})
    sessions = [s for s in sessions if s["window_end"] >= now]
    while len(sessions) > CAP:
        # merge closest pair else drop lowest area*intensity
        bi, bj, bd = None, None, 1e18
        for i in range(len(sessions)):
            for j in range(i + 1, len(sessions)):
                d = dist(sessions[i]["bbox"], sessions[j]["bbox"])
                if d < bd:
                    bi, bj, bd = i, j, d
        a, b = sessions[bi]["bbox"], sessions[bj]["bbox"]
        union = [min(a[0], b[0]), min(a[1], b[1]), max(a[2], b[2]), max(a[3], b[3])]
        area = (union[2] - union[0]) * (union[3] - union[1]) * 12321
        if area <= MAX_KM2:
            sessions[bi] = {"id": f"m_{now}_{bi}", "bbox": union,
                            "window_end": max(sessions[bi]["window_end"],
                                              sessions[bj]["window_end"]), "history": []}
            sessions.pop(bj)
        else:
            sessions.pop(bj)
    return sessions
