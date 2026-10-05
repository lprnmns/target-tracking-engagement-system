"""Disposable DirectShow camera probe for Windows camera inventory."""
from __future__ import annotations

import argparse
import json
import os
import time
from pathlib import Path


def _parser():
    parser = argparse.ArgumentParser()
    parser.add_argument("--index", type=int, required=True)
    parser.add_argument("--backend", choices=("dshow", "msmf", "any"), default="dshow")
    parser.add_argument("--output", type=Path)
    parser.add_argument("--capabilities", action="store_true")
    return parser


def _backend(cv2, name: str) -> int:
    return {"dshow": cv2.CAP_DSHOW, "msmf": cv2.CAP_MSMF, "any": cv2.CAP_ANY}[name]


def _probe_mode(cv2, index: int, backend: int, width: int, height: int):
    capture = cv2.VideoCapture(index, backend)
    try:
        if not capture.isOpened():
            return None, 0.0, None, 0
        # Match the live worker's DirectShow negotiation order.  On this UVC
        # camera, FOURCC before dimensions selects a ~5 FPS 1080p mode while
        # dimensions/FPS followed by MJPG sustains 30 FPS.
        capture.set(cv2.CAP_PROP_FRAME_WIDTH, width)
        capture.set(cv2.CAP_PROP_FRAME_HEIGHT, height)
        capture.set(cv2.CAP_PROP_FPS, 30)
        capture.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter_fourcc(*"MJPG"))
        capture.set(cv2.CAP_PROP_BUFFERSIZE, 1)
        for _ in range(3):
            capture.read()
        frames = []
        started = time.monotonic()
        deadline = started + 2.0
        while len(frames) < 8 and time.monotonic() < deadline:
            ok, frame = capture.read()
            if ok and frame is not None:
                frames.append(frame)
        elapsed = max(time.monotonic() - started, 1e-6)
        if len(frames) < 6:
            return None, float(capture.get(cv2.CAP_PROP_FPS) or 0), None, len(frames)
        frame = frames[-1]
        actual = (int(frame.shape[1]), int(frame.shape[0]))
        measured = len(frames) / elapsed
        reported = float(capture.get(cv2.CAP_PROP_FPS) or 0)
        return actual, max(measured, reported), frame.copy(), len(frames)
    finally:
        capture.release()


def _capabilities(cv2, index: int, backend: int, output: Path | None) -> int:
    candidates = ((3840, 2160), (2560, 1440), (1920, 1080), (1600, 1200),
                  (1280, 960), (1280, 720), (960, 540), (640, 480), (640, 360))
    supported = set()
    fps_values = set()
    evidence = []
    best_frame = None
    for width, height in candidates:
        actual, fps, frame, frame_count = _probe_mode(cv2, index, backend, width, height)
        evidence.append({"requested": f"{width}x{height}", "accepted": actual is not None,
                         "actual": f"{actual[0]}x{actual[1]}" if actual else None,
                         "frames": frame_count, "measured_fps": round(fps, 2)})
        if actual is None:
            continue
        supported.add(actual)
        if fps > 0:
            fps_values.add(int(round(fps)))
        if best_frame is None or actual[0] * actual[1] > best_frame.shape[1] * best_frame.shape[0]:
            best_frame = frame
        if actual == (width, height):
            break
    if output is not None and best_frame is not None:
        output.parent.mkdir(parents=True, exist_ok=True)
        cv2.imwrite(str(output), best_frame)
    ordered = sorted(supported, key=lambda item: (item[0] * item[1], item[0]), reverse=True)
    print(json.dumps({
        "accepted": bool(ordered),
        "sustainable": bool(ordered),
        "supported_resolutions": [f"{w}x{h}" for w, h in ordered],
        "supported_fps": sorted(fps_values or {30}),
        "actual_width": ordered[0][0] if ordered else None,
        "actual_height": ordered[0][1] if ordered else None,
        "actual_fps": max(fps_values) if fps_values else 30,
        "pixel_formats": ["MJPG"],
        "mode_evidence": evidence,
    }))
    return 0 if ordered else 1


def main() -> int:
    args = _parser().parse_args()
    if os.name != "nt":
        print(json.dumps({"accepted": False, "supported_resolutions": [], "supported_fps": []}) if args.capabilities else "0")
        return 1
    import cv2  # type: ignore[import-not-found]
    backend = _backend(cv2, args.backend)
    if args.capabilities:
        return _capabilities(cv2, args.index, backend, args.output)
    capture = cv2.VideoCapture(args.index, backend)
    try:
        if not capture.isOpened():
            print("0")
            return 1
        capture.set(cv2.CAP_PROP_FRAME_WIDTH, 640)
        capture.set(cv2.CAP_PROP_FRAME_HEIGHT, 480)
        capture.set(cv2.CAP_PROP_FPS, 30)
        capture.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter_fourcc(*"MJPG"))
        capture.set(cv2.CAP_PROP_BUFFERSIZE, 1)
        for _ in range(3):
            ok, frame = capture.read()
            if ok and frame is not None:
                if args.output is not None:
                    args.output.parent.mkdir(parents=True, exist_ok=True)
                    cv2.imwrite(str(args.output), frame)
                print("1")
                return 0
        print("0")
        return 1
    finally:
        capture.release()


if __name__ == "__main__":
    raise SystemExit(main())
