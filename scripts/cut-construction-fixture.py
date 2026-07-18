#!/usr/bin/env python3
"""Cut a 60s demo fixture from a real rosbag2 MCAP recording.

Produces fixtures/construction_indoor_60s.mcap from a large construction-
robot bag: telemetry topics are copied through byte-for-byte (original
schemas + CDR payloads), while the raw camera stream is transcoded
sensor_msgs/Image (bgr8) -> sensor_msgs/CompressedImage (JPEG) so the web
replay's video track can render it (the worker only classifies
CompressedImage as video). The OAK-D depth stream (8UC1) is colormapped
into a second, honestly-named video track (/oakd/depth/colorized/...)
and decimated to match the RGB rate.

Privacy handling baked in:
  - An optional feathered gaussian blur over a horizontal band of the
    frame for a given time range (--blur-band), used to anonymize distant
    people visible through the open building edge.
  - GPS/NMEA topics are NOT in the default topic set: raw ublox strings
    carry real lat/lon of the recording site.

Not a runtime component — a one-time authoring tool. Needs:
    pip install mcap mcap-ros2-support pillow numpy

Example (the shipped construction_indoor_60s.mcap):
    python scripts/cut-construction-fixture.py \
        --bag ~/Projects/Mcap_construction_2/construction_data_1_0.mcap \
        --start-ns 1784195407265000192 --duration 60 \
        --blur-band 30.0:48.5:130:290
"""

from __future__ import annotations

import argparse
import io
import struct
import sys
from pathlib import Path

import numpy as np
from mcap.reader import make_reader
from mcap.writer import Writer
from mcap_ros2.decoder import DecoderFactory
from PIL import Image, ImageDraw, ImageFilter

REPO_ROOT = Path(__file__).resolve().parent.parent

# Telemetry copied through unchanged. Deliberately excludes:
#   /livox/lidar            PointCloud2 - not rendered, dominates file size
#   /oakd/depth/image_raw   raw 8UC1 - not rendered, huge
#   /serial_port_ublox/*    NMEA strings with real site coordinates
PASSTHROUGH_TOPICS = [
    "/dlio/odom_node/odom",
    "/odom/local",
    "/imu",
    "/livox/imu",
]

IMAGE_TOPIC = "/oakd/rgb/image_raw"
OUT_IMAGE_TOPIC = "/oakd/rgb/image_raw/compressed"

DEPTH_TOPIC = "/oakd/depth/image_raw"
OUT_DEPTH_TOPIC = "/oakd/depth/colorized/compressed"


def _depth_lut() -> "np.ndarray":
    """Turbo-like gradient: near = warm, far = cool, no-return = near-black."""
    stops = [
        (0, (15, 10, 40)),
        (64, (62, 155, 254)),
        (128, (53, 233, 148)),
        (192, (248, 213, 53)),
        (255, (220, 47, 32)),
    ]
    lut = np.zeros((256, 3), np.uint8)
    for (a, ca), (b, cb) in zip(stops[:-1], stops[1:]):
        for i in range(a, b + 1):
            f = (i - a) / (b - a)
            lut[i] = [int(ca[k] + (cb[k] - ca[k]) * f) for k in range(3)]
    # this stream is disparity-like (large raw value = near), so the warm
    # end of the gradient lands on near surfaces as-is
    return lut

COMPRESSED_IMAGE_SCHEMA = """\
std_msgs/Header header
string format
uint8[] data
================================================================================
MSG: std_msgs/Header
builtin_interfaces/Time stamp
string frame_id
================================================================================
MSG: builtin_interfaces/Time
int32 sec
uint32 nanosec
"""

CDR_HEADER = b"\x00\x01\x00\x00"  # little-endian encapsulation


def encode_compressed_image(sec: int, nanosec: int, frame_id: str, jpeg: bytes) -> bytes:
    """CDR-encode sensor_msgs/msg/CompressedImage.

    Alignment offsets are relative to the byte after the 4-byte
    encapsulation header.
    """
    buf = bytearray()

    def pad_to(align: int) -> None:
        while len(buf) % align:
            buf.append(0)

    def put_string(s: str) -> None:
        pad_to(4)
        raw = s.encode() + b"\x00"
        buf.extend(struct.pack("<I", len(raw)))
        buf.extend(raw)

    buf.extend(struct.pack("<iI", sec, nanosec))
    put_string(frame_id)
    put_string("jpeg")
    pad_to(4)
    buf.extend(struct.pack("<I", len(jpeg)))
    buf.extend(jpeg)
    return CDR_HEADER + bytes(buf)


def parse_blur_band(spec: str) -> tuple[float, float, int, int]:
    """start_s:end_s:y0:y1 in window-relative seconds / source pixels."""
    start_s, end_s, y0, y1 = spec.split(":")
    return float(start_s), float(end_s), int(y0), int(y1)


def blur_band(img: Image.Image, y0: int, y1: int, radius: int) -> Image.Image:
    blurred = img.filter(ImageFilter.GaussianBlur(radius))
    mask = Image.new("L", img.size, 0)
    ImageDraw.Draw(mask).rectangle([0, y0, img.width, y1], fill=255)
    mask = mask.filter(ImageFilter.GaussianBlur(12))
    return Image.composite(blurred, img, mask)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--bag", required=True, help="source rosbag2 MCAP file")
    ap.add_argument("--start-ns", type=int, required=True, help="window start, epoch ns")
    ap.add_argument("--duration", type=float, default=60.0, help="window length, seconds")
    ap.add_argument(
        "--out",
        default=str(REPO_ROOT / "fixtures" / "construction_indoor_60s.mcap"),
    )
    ap.add_argument("--robot-id", default="construction-bot-01")
    ap.add_argument("--subsystem", default="construction")
    ap.add_argument("--label", default="manual")
    ap.add_argument("--width", type=int, default=480, help="output frame width")
    ap.add_argument("--height", type=int, default=360, help="output frame height")
    ap.add_argument("--quality", type=int, default=70, help="JPEG quality")
    ap.add_argument(
        "--blur-band",
        action="append",
        default=[],
        metavar="START:END:Y0:Y1",
        help="feathered blur over source-pixel rows y0..y1 for window seconds "
        "start..end (repeatable); used to anonymize distant people",
    )
    ap.add_argument("--blur-radius", type=int, default=9)
    ap.add_argument(
        "--depth-decimate",
        type=int,
        default=2,
        help="keep every Nth depth frame (2 matches the 20 Hz stream to the "
        "10 Hz RGB); 0 skips the depth track entirely",
    )
    ap.add_argument(
        "--depth-max",
        type=int,
        default=25,
        help="raw 8UC1 value mapped to the far end of the colormap "
        "(this bag's depth values span 0..24)",
    )
    args = ap.parse_args()

    start_ns = args.start_ns
    end_ns = start_ns + int(args.duration * 1e9)
    bands = [parse_blur_band(s) for s in args.blur_band]
    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    wanted = set(PASSTHROUGH_TOPICS) | {IMAGE_TOPIC}
    if args.depth_decimate > 0:
        wanted.add(DEPTH_TOPIC)
    counts: dict[str, int] = {}
    blurred_frames = 0
    depth_seen = 0
    depth_lut = _depth_lut()

    with open(args.bag, "rb") as src, open(out_path, "wb") as dst:
        reader = make_reader(src, decoder_factories=[DecoderFactory()])
        writer = Writer(dst)
        writer.start(profile="ros2", library="missiondebug cut-construction-fixture")
        writer.add_metadata(
            name="missiondebug",
            data={
                "robot_id": args.robot_id,
                "label": args.label,
                "subsystem": args.subsystem,
            },
        )

        channel_for: dict[str, int] = {}

        def channel(topic: str, schema_name: str, schema_data: bytes) -> int:
            if topic not in channel_for:
                schema_id = writer.register_schema(
                    name=schema_name, encoding="ros2msg", data=schema_data
                )
                channel_for[topic] = writer.register_channel(
                    schema_id=schema_id, topic=topic, message_encoding="cdr"
                )
            return channel_for[topic]

        for schema, chan, message, ros_msg in reader.iter_decoded_messages(
            topics=sorted(wanted), start_time=start_ns, end_time=end_ns
        ):
            if chan.topic == IMAGE_TOPIC:
                if ros_msg.encoding != "bgr8":
                    raise SystemExit(f"unexpected image encoding {ros_msg.encoding}")
                img = Image.frombytes("RGB", (ros_msg.width, ros_msg.height), bytes(ros_msg.data))
                b, g, r = img.split()
                img = Image.merge("RGB", (r, g, b))

                off_s = (message.log_time - start_ns) / 1e9
                for band_start, band_end, y0, y1 in bands:
                    if band_start <= off_s <= band_end:
                        img = blur_band(img, y0, y1, args.blur_radius)
                        blurred_frames += 1
                        break

                img = img.resize((args.width, args.height), Image.LANCZOS)
                jpeg_buf = io.BytesIO()
                img.save(jpeg_buf, "JPEG", quality=args.quality)

                payload = encode_compressed_image(
                    ros_msg.header.stamp.sec,
                    ros_msg.header.stamp.nanosec,
                    ros_msg.header.frame_id,
                    jpeg_buf.getvalue(),
                )
                cid = channel(
                    OUT_IMAGE_TOPIC,
                    "sensor_msgs/msg/CompressedImage",
                    COMPRESSED_IMAGE_SCHEMA.encode(),
                )
                out_topic = OUT_IMAGE_TOPIC
            elif chan.topic == DEPTH_TOPIC:
                depth_seen += 1
                if (depth_seen - 1) % args.depth_decimate:
                    continue
                if ros_msg.encoding != "8UC1":
                    raise SystemExit(f"unexpected depth encoding {ros_msg.encoding}")
                arr = np.frombuffer(bytes(ros_msg.data), np.uint8).reshape(
                    ros_msg.height, ros_msg.width
                )
                stretched = np.clip(
                    arr.astype(np.float32) * (255.0 / args.depth_max), 0, 255
                ).astype(np.uint8)
                img = Image.fromarray(depth_lut[stretched])
                img = img.resize(
                    (args.width, args.width * ros_msg.height // ros_msg.width),
                    Image.LANCZOS,
                )
                jpeg_buf = io.BytesIO()
                img.save(jpeg_buf, "JPEG", quality=args.quality)

                payload = encode_compressed_image(
                    ros_msg.header.stamp.sec,
                    ros_msg.header.stamp.nanosec,
                    ros_msg.header.frame_id,
                    jpeg_buf.getvalue(),
                )
                cid = channel(
                    OUT_DEPTH_TOPIC,
                    "sensor_msgs/msg/CompressedImage",
                    COMPRESSED_IMAGE_SCHEMA.encode(),
                )
                out_topic = OUT_DEPTH_TOPIC
            else:
                cid = channel(chan.topic, schema.name, schema.data)
                payload = message.data
                out_topic = chan.topic

            writer.add_message(
                channel_id=cid,
                log_time=message.log_time,
                publish_time=message.publish_time,
                data=payload,
                sequence=0,
            )
            counts[out_topic] = counts.get(out_topic, 0) + 1

        writer.finish()

    size_mb = out_path.stat().st_size / 1024 / 1024
    print(f"wrote {out_path} ({size_mb:.1f} MB)")
    for topic in sorted(counts):
        print(f"  {topic:36s} {counts[topic]:6d} msgs")
    if bands:
        print(f"  privacy blur applied to {blurred_frames} frames")
    return 0


if __name__ == "__main__":
    sys.exit(main())
