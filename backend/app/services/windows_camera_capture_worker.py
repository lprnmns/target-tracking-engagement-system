"""Isolated Windows DirectShow frame producer.

This process intentionally contains no backend/Gateway state. If a vendor UVC
driver crashes OpenCV during hot-unplug, Windows terminates only this helper.
"""

from __future__ import annotations

import argparse
import mmap
import os
import struct
import time
from pathlib import Path

import cv2


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--index", type=int, required=True)
    parser.add_argument("--width", type=int, required=True)
    parser.add_argument("--height", type=int, required=True)
    parser.add_argument("--fps", type=int, required=True)
    parser.add_argument("--pixel-format", default="MJPG")
    parser.add_argument("--exposure-auto", type=int, choices=(0, 1), default=None)
    parser.add_argument("--exposure", type=float, default=None)
    parser.add_argument("--brightness", type=float, default=None)
    parser.add_argument("--contrast", type=float, default=None)
    parser.add_argument("--gain", type=float, default=None)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--shared-memory-tag", default=None)
    parser.add_argument("--shared-memory-size", type=int, default=8 * 1024 * 1024)
    parser.add_argument("--shared-memory-format", choices=("jpeg", "raw"), default="jpeg")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    capture = cv2.VideoCapture(args.index, cv2.CAP_DSHOW)
    # DirectShow UVC drivers negotiate the stream when width/height/FPS are
    # set.  Setting FOURCC first makes this camera fall back to a slow
    # uncompressed 1920x1080 mode (about 5 FPS), even though the same camera
    # delivers MJPG 1080p30 in Windows Camera.  Negotiate dimensions and rate
    # first, then select MJPG, and only then constrain buffering.
    capture.set(cv2.CAP_PROP_FRAME_WIDTH, args.width)
    capture.set(cv2.CAP_PROP_FRAME_HEIGHT, args.height)
    capture.set(cv2.CAP_PROP_FPS, args.fps)
    if args.pixel_format == "MJPG":
        capture.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter_fourcc(*"MJPG"))
    elif args.pixel_format == "YUYV":
        capture.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter_fourcc(*"YUYV"))
    capture.set(cv2.CAP_PROP_BUFFERSIZE, 1)
    # DirectShow exposes these controls through OpenCV with driver-specific
    # ranges.  In DirectShow, setting CAP_PROP_AUTO_EXPOSURE (e.g. to 0.75)
    # disables the UVC camera's native hardware ISP auto-exposure loop and
    # crushes brightness. Leave exposure to hardware ISP unless manual
    # exposure is explicitly requested (matching Light goruntu_motoru.py).
    if args.exposure_auto == 0 and args.exposure is not None:
        capture.set(cv2.CAP_PROP_AUTO_EXPOSURE, 0.25)
        capture.set(cv2.CAP_PROP_EXPOSURE, args.exposure)
    if args.brightness is not None:
        capture.set(cv2.CAP_PROP_BRIGHTNESS, args.brightness)
    if args.contrast is not None:
        capture.set(cv2.CAP_PROP_CONTRAST, args.contrast)
    if args.gain is not None:
        capture.set(cv2.CAP_PROP_GAIN, args.gain)
    if not capture.isOpened():
        return 2
    args.output.parent.mkdir(parents=True, exist_ok=True)
    shared = None
    if args.shared_memory_tag:
        try:
            shared = mmap.mmap(-1, int(args.shared_memory_size), tagname=args.shared_memory_tag)
        except (OSError, ValueError):
            capture.release()
            return 5
    # Give UVC auto-exposure/white-balance a short settling window before the
    # first evidence frame. Without this, the preview often starts as a dark
    # stale-looking image and the detector misses the first target.
    for _ in range(8):
        capture.read()
        time.sleep(0.02)
    failures = 0
    black_frames = 0
    try:
        while True:
            ok, frame = capture.read()
            if not ok or frame is None:
                failures += 1
                if failures >= 5:
                    return 3
                time.sleep(0.03)
                continue
            failures = 0
            # Do not scan every pixel just to reject a synthetic all-black
            # frame. At 1080p that full-frame NumPy allocation became the
            # capture bottleneck (roughly 9 FPS before inference). A sparse
            # sample is sufficient for the unplug/black-frame guard and keeps
            # the worker close to the camera's native 30 FPS.
            sample = frame[::16, ::16]
            bright_fraction = float((sample > 8).mean())
            if float(sample.mean()) < 2.0 and bright_fraction < 0.005:
                black_frames += 1
                if black_frames >= 15:
                    # Several Windows UVC drivers keep returning synthetic
                    # black frames after hot-unplug instead of failing read().
                    return 4
                time.sleep(0.01)
                continue
            black_frames = 0
            if shared is not None:
                if args.shared_memory_format == "raw":
                    frame_width, frame_height = int(frame.shape[1]), int(frame.shape[0])
                    payload_length = int(frame.nbytes)
                else:
                    # JPEG remains available as a compatibility transport for
                    # older callers, but raw shared memory is the fast path.
                    encoded_ok, encoded = cv2.imencode(".jpg", frame, [int(cv2.IMWRITE_JPEG_QUALITY), 95])
                    if not encoded_ok:
                        continue
                    payload = encoded.tobytes()
                    payload_length = len(payload)
                    frame_width, frame_height = 0, 0
                if payload_length > int(args.shared_memory_size) - 16:
                    continue
                # Seqlock: odd means a write is in progress; the parent only
                # decodes a payload when the sequence is unchanged and even.
                sequence = struct.unpack_from("<Q", shared, 0)[0]
                if sequence & 1:
                    sequence += 1
                struct.pack_into("<Q", shared, 0, sequence + 1)
                struct.pack_into("<I", shared, 8, payload_length)
                struct.pack_into("<HH", shared, 12, frame_width, frame_height)
                if args.shared_memory_format == "raw":
                    # mmap.write is a native memcpy and avoids creating a
                    # second 6 MiB Python bytes object for every 1080p frame.
                    shared.seek(16)
                    shared.write(memoryview(frame).cast("B"))
                else:
                    shared[16 : 16 + payload_length] = payload
                struct.pack_into("<Q", shared, 0, sequence + 2)
            else:
                # Compatibility file transport for hosts without named
                # mappings. It is intentionally not used by the Windows
                # runtime when the raw shared mapping is available.
                encoded_ok, encoded = cv2.imencode(".jpg", frame, [int(cv2.IMWRITE_JPEG_QUALITY), 95])
                if not encoded_ok:
                    continue
                payload = encoded.tobytes()
                temporary = args.output.with_suffix(".jpg.tmp")
                try:
                    temporary.write_bytes(payload)
                    replaced = False
                    for _attempt in range(10):
                        try:
                            os.replace(temporary, args.output)
                            replaced = True
                            break
                        except PermissionError:
                            # Compatibility fallback only; the normal shared
                            # mapping path has no file-lock contention.
                            time.sleep(0.005)
                    if not replaced:
                        temporary.unlink(missing_ok=True)
                except OSError:
                    temporary.unlink(missing_ok=True)
                    time.sleep(0.01)
    finally:
        capture.release()
        if shared is not None:
            shared.close()


if __name__ == "__main__":
    raise SystemExit(main())
